import os
import json
import re
from subprocess import run, PIPE
import py_compile


def parse_unified_diff(diff):
    """Parse unified diff to extract added lines and text by file."""
    added_lines_by_file = {}
    added_text_by_file = {}
    current_file = None
    current_lines = []
    current_text = []

    for line in diff.split('\n'):
        if line.startswith('diff --git'):
            if current_file and current_lines:
                added_lines_by_file[current_file] = current_lines
                added_text_by_file[current_file] = '\n'.join(current_text)
            current_file = line.split('b/')[-1]
            current_lines = []
            current_text = []
        elif line.startswith('@@'):
            match = re.match(r'@@ -(\d+),?\d* \+(\d+),?\d* @@', line)
            if match:
                current_line = int(match.group(2))
        elif line.startswith('+') and not line.startswith('+++') and current_file:
            current_lines.append(current_line)
            current_text.append(line[1:])
            current_line += 1
        elif line.startswith(' ') and current_file:
            current_line += 1

    if current_file and current_lines:
        added_lines_by_file[current_file] = current_lines
        added_text_by_file[current_file] = '\n'.join(current_text)

    return added_lines_by_file, added_text_by_file


def rule_based_review(file_path, added_lines):
    """Placeholder for rule-based review (implement as needed)."""
    return []


def run_syntax_checks(file_path: str):
    """Run language-specific syntax/lint checks and return issues."""
    results = []
    ext = os.path.splitext(file_path)[1]

    try:
        if ext in (".js", ".jsx", ".ts", ".tsx"):
            p = run(["eslint", "-f", "json", file_path],
                    stdout=PIPE, stderr=PIPE, text=True)
            if p.returncode != 0 or p.stdout.strip():
                try:
                    eslint_output = json.loads(p.stdout)
                    for msg in eslint_output[0].get("messages", []):
                        results.append({
                            "file": file_path,
                            "line": int(msg.get("line", 1)),
                            "comment": msg.get("message", "Lint issue")
                        })
                except json.JSONDecodeError:
                    results.append({
                        "file": file_path,
                        "line": 1,
                        "comment": f"ESLint failed: {p.stderr.strip()}"
                    })
        elif ext == ".py":
            try:
                py_compile.compile(file_path, doraise=True)
            except py_compile.PyCompileError as e:
                line_msg = str(e).split(',')[0] if ',' in str(e) else "1"
                results.append({
                    "file": file_path,
                    "line": int(line_msg.split()[-1]) if line_msg.isdigit() else 1,
                    "comment": f"Python syntax error: {str(e)}"
                })
            except Exception as py_e:
                results.append({
                    "file": file_path,
                    "line": 1,
                    "comment": f"Python compile failed: {str(py_e)}"
                })
    except Exception as e:
        print(f"Syntax check error for {file_path}: {str(e)}")
        results.append({
            "file": file_path,
            "line": 1,
            "comment": f"Syntax check failed: {str(e)}"
        })

    return results


def safe_extract_json(text: str):
    """Extract and parse JSON array reliably from model output."""
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            snippet = text[start:end+1]
            try:
                return json.loads(snippet)
            except Exception:
                fixed = re.sub(r"(\w+):", r'"\1":', snippet)
                fixed = fixed.replace("'", '"')
                try:
                    return json.loads(fixed)
                except Exception:
                    pass
    return []


def extract_snippet(diff, line_number, file_path):
    """Extract a code snippet around the given line number from the diff."""
    lines = diff.split('\n')
    snippet = []
    current_line = None
    for i, line in enumerate(lines):
        if line.startswith('@@'):
            match = re.match(r'@@ -(\d+),?\d* \+(\d+),?\d* @@', line)
            if match:
                current_line = int(match.group(2))
        elif line.startswith(('+', '-', ' ')) and current_line is not None:
            if abs(current_line - line_number) <= 2:
                snippet.append(line)
            if line.startswith('+'):
                if current_line == line_number:
                    return '\n'.join(snippet)
                current_line += 1
            elif line.startswith(' '):
                current_line += 1
    return '\n'.join(snippet) if snippet else f"No snippet found for line {line_number} in {file_path}."


def review_code(diff):
    if not diff or diff.startswith("Error"):
        return []

    added_lines_by_file, added_text_by_file = parse_unified_diff(diff)
    final_results = []

    for file_path, added_lines in added_lines_by_file.items():
        # 1️⃣ Run syntax/lint checks
        syntax_items = run_syntax_checks(file_path)
        final_results.extend(syntax_items)

        # 2️⃣ Run rule-based checks
        rule_based_items = rule_based_review(file_path, added_lines)
        final_results.extend(rule_based_items)

        # 3️⃣ Run LLM review
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                full_code = f.read()
        except Exception as e:
            print(f"Failed to read {file_path}: {str(e)}")
            final_results.append({
                "file": file_path,
                "line": 1,
                "comment": f"Failed to read file: {str(e)}"
            })
            continue

        ext = os.path.splitext(file_path)[1]
        language = "Python" if ext == ".py" else "JavaScript/React" if ext in (
            ".js", ".jsx", ".ts", ".tsx") else "Unknown"
        prompt = (
            f"You are an expert {language} code reviewer. Review the following file for:\n"
            "- Syntax errors\n"
            "- Logical bugs (boundary conditions, off-by-one, wrong variables)\n"
            "- Performance issues\n"
            "- Security concerns (secrets, injection, unsafe code)\n"
            "- Maintainability and readability\n\n"
            f"File: {file_path}\n\n"
            f"```{full_code}```\n\n"
            "Return ONLY a valid JSON array with no extra text. Each object must have: "
            '{"file": "relative/path", "line": <line_number>, "comment": "specific suggestion"}'
        )

        try:
            from ollama import Client
            client = Client(host='http://127.0.0.1:11434')
            response = client.generate(
                model='codellama:7b-instruct', prompt=prompt)
            raw_text = response.get("response", "").strip()

            items = safe_extract_json(raw_text)
            for item in items:
                if isinstance(item.get("line"), str):
                    try:
                        item["line"] = int(item["line"])
                    except ValueError:
                        item["line"] = 1
                # Add snippet and resolve option
                if item.get("file") == file_path and item.get("line"):
                    snippet = extract_snippet(diff, item["line"], file_path)
                    item["comment"] = (
                        f"```diff\n{snippet}\n```\n\n{item['comment']}\n\n**Resolve:** Mark as resolved in GitHub UI"
                    )
                final_results.append(item)
            if not items:
                print(
                    f"LLM produced empty/invalid JSON for {file_path}: {raw_text[:200]}...")
                final_results.append({
                    "file": file_path,
                    "line": 1,
                    "comment": f"No specific AI comments generated.\n\n**Resolve:** Mark as resolved in GitHub UI"
                })
        except Exception as e:
            print(f"LLM review failed for {file_path}: {str(e)}")
            final_results.append({
                "file": file_path,
                "line": 1,
                "comment": f"AI review failed: {str(e)}\n\n**Resolve:** Mark as resolved in GitHub UI"
            })

    return final_results


def save_review_results(results):
    """Save review results to review.json."""
    try:
        with open("review.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
    except Exception as e:
        print(f"Failed to save review.json: {str(e)}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--github':
        diff = os.environ.get('PR_DIFF', '')
    else:
        diff = parse_unified_diff(os.popen('git diff main').read())[0]

    if not diff:
        print("No changes detected.")
        return

    results = review_code(diff)
    save_review_results(results)
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
