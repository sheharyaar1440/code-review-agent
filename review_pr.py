import os
import json
import re
import sys
from subprocess import run, PIPE
import py_compile
import git
import time
from ollama import Client, OllamaError


def parse_unified_diff(diff):
    print("Starting diff parsing...")
    start_time = time.time()
    added_lines_by_file = {}
    added_text_by_file = {}
    current_file = None
    current_lines = []
    current_text = []

    try:
        if len(diff) > 100000:
            print("Diff too large, truncating...")
            diff = diff[:100000]
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
    except Exception as e:
        print(f"Error parsing diff: {str(e)}")
        return {}, {}
    print(f"Diff parsing completed in {time.time() - start_time:.2f} seconds")
    return added_lines_by_file, added_text_by_file


def rule_based_review(file_path, added_lines):
    return []


def run_syntax_checks(file_path: str):
    print(f"Running syntax checks for {file_path}...")
    start_time = time.time()
    results = []
    ext = os.path.splitext(file_path)[1]

    try:
        if ext in (".js", ".jsx", ".ts", ".tsx"):
            p = run(["eslint", "-f", "json", file_path],
                    stdout=PIPE, stderr=PIPE, text=True, timeout=30)
            if p.returncode != 0 or p.stdout.strip():
                try:
                    eslint_output = json.loads(p.stdout)
                    for msg in eslint_output[0].get("messages", []):
                        results.append({
                            "file": file_path,
                            "line": int(msg.get("line", 1)),
                            "snippet": "",
                            "comment": msg.get("message", "Lint issue")
                        })
                except json.JSONDecodeError:
                    results.append({
                        "file": file_path,
                        "line": 1,
                        "snippet": "",
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
                    "snippet": "",
                    "comment": f"Python syntax error: {str(e)}"
                })
            except Exception as py_e:
                results.append({
                    "file": file_path,
                    "line": 1,
                    "snippet": "",
                    "comment": f"Python compile failed: {str(py_e)}"
                })
    except Exception as e:
        print(f"Syntax check error for {file_path}: {str(e)}")
        results.append({
            "file": file_path,
            "line": 1,
            "snippet": "",
            "comment": f"Syntax check failed: {str(e)}"
        })
    print(
        f"Syntax checks for {file_path} completed in {time.time() - start_time:.2f} seconds")
    return results


def safe_extract_json(text: str):
    print("Extracting JSON from LLM output...")
    start_time = time.time()
    text = text.strip()
    try:
        result = json.loads(text)
        print(
            f"JSON extraction completed in {time.time() - start_time:.2f} seconds")
        return result
    except Exception:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            snippet = text[start:end+1]
            try:
                result = json.loads(snippet)
                print(
                    f"JSON extraction (snippet) completed in {time.time() - start_time:.2f} seconds")
                return result
            except Exception:
                fixed = re.sub(r"(\w+):", r'"\1":', snippet)
                fixed = fixed.replace("'", '"')
                try:
                    result = json.loads(fixed)
                    print(
                        f"JSON extraction (fixed) completed in {time.time() - start_time:.2f} seconds")
                    return result
                except Exception:
                    pass
    print(f"JSON extraction failed in {time.time() - start_time:.2f} seconds")
    return []


def extract_snippet(diff, line_number, file_path):
    print(f"Extracting snippet for {file_path}:{line_number}...")
    start_time = time.time()
    lines = diff.split('\n')
    snippet = []
    current_line = None
    try:
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
                        print(
                            f"Snippet extraction completed in {time.time() - start_time:.2f} seconds")
                        return '\n'.join(snippet)
                    current_line += 1
                elif line.startswith(' '):
                    current_line += 1
    except Exception as e:
        print(
            f"Error extracting snippet for {file_path}:{line_number}: {str(e)}")
        return ""
    print(
        f"Snippet extraction completed in {time.time() - start_time:.2f} seconds")
    return '\n'.join(snippet) if snippet else ""


def review_code(diff):
    print("Starting code review...")
    start_time = time.time()
    if not diff or diff.startswith("Error"):
        print("No valid diff provided.")
        return [{
            "file": "unknown",
            "line": 1,
            "snippet": "",
            "comment": "No valid diff provided.\n\n**Resolve:** Mark as resolved in GitHub UI"
        }]

    final_results = []
    added_lines_by_file, added_text_by_file = parse_unified_diff(diff)

    if not added_lines_by_file:
        print("No files with changes detected in diff.")
        final_results.append({
            "file": "unknown",
            "line": 1,
            "snippet": "",
            "comment": "No files with changes detected.\n\n**Resolve:** Mark as resolved in GitHub UI"
        })
        print(
            f"Code review completed in {time.time() - start_time:.2f} seconds")
        save_review_results(final_results)
        return final_results

    for file_path, added_lines in added_lines_by_file.items():
        print(f"Reviewing file: {file_path}")
        # 1️⃣ Run syntax/lint checks
        syntax_items = run_syntax_checks(file_path)
        final_results.extend(syntax_items)

        # 2️⃣ Run rule-based checks
        rule_based_items = rule_based_review(file_path, added_lines)
        final_results.extend(rule_based_items)

        # 3️⃣ Run LLM review
        try:
            print(f"Reading file {file_path}...")
            with open(file_path, "r", encoding="utf-8") as f:
                full_code = f.read()
            if len(full_code) > 10000:
                print(f"File {file_path} too large, truncating...")
                full_code = full_code[:10000]
        except Exception as e:
            print(f"Failed to read {file_path}: {str(e)}")
            final_results.append({
                "file": file_path,
                "line": 1,
                "snippet": "",
                "comment": f"Failed to read file: {str(e)}\n\n**Resolve:** Mark as resolved in GitHub UI"
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
            '{"file": "relative/path", "line": <line_number>, "snippet": "<code snippet>", "comment": "specific suggestion"}. '
            "Ensure the response is valid JSON with double quotes and no markdown."
        )

        try:
            print(f"Connecting to Ollama for {file_path}...")
            client = Client(host='http://127.0.0.1:11434')
            response = client.generate(
                model='codellama:7b-instruct', prompt=prompt, options={'timeout': 60})
            raw_text = response.get("response", "").strip()
            print(f"Raw LLM output for {file_path}: {raw_text[:200]}...")

            items = safe_extract_json(raw_text)
            for item in items:
                if isinstance(item.get("line"), str):
                    try:
                        item["line"] = int(item["line"])
                    except ValueError:
                        item["line"] = 1
                if item.get("file") == file_path and item.get("line"):
                    snippet = extract_snippet(diff, item["line"], file_path)
                    item["snippet"] = snippet
                    item["comment"] = item.get(
                        "comment", "") + "\n\n**Resolve:** Mark as resolved in GitHub UI"
                final_results.append(item)
            if not items:
                print(
                    f"LLM produced empty/invalid JSON for {file_path}: {raw_text[:200]}...")
                final_results.append({
                    "file": file_path,
                    "line": 1,
                    "snippet": "",
                    "comment": f"No specific AI comments generated.\n\n**Resolve:** Mark as resolved in GitHub UI"
                })
        except OllamaError as e:
            print(f"LLM review failed for {file_path}: {str(e)}")
            final_results.append({
                "file": file_path,
                "line": 1,
                "snippet": "",
                "comment": f"AI review failed: {str(e)}\n\n**Resolve:** Mark as resolved in GitHub UI"
            })
        except Exception as e:
            print(f"Unexpected error in LLM review for {file_path}: {str(e)}")
            final_results.append({
                "file": file_path,
                "line": 1,
                "snippet": "",
                "comment": f"Unexpected error: {str(e)}\n\n**Resolve:** Mark as resolved in GitHub UI"
            })

    print(f"Code review completed in {time.time() - start_time:.2f} seconds")
    save_review_results(final_results)
    return final_results


def save_review_results(results):
    print("Saving review.json...")
    start_time = time.time()
    try:
        # Ensure the file is saved in the current working directory
        with open("review.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(
            f"Successfully saved review.json in {time.time() - start_time:.2f} seconds to {os.getcwd()}")
    except Exception as e:
        print(f"Failed to save review.json: {str(e)}")
        with open("review.json", "w", encoding="utf-8") as f:
            json.dump([{
                "file": "unknown",
                "line": 1,
                "snippet": "",
                "comment": f"Failed to save review: {str(e)}\n\n**Resolve:** Mark as resolved in GitHub UI"
            }], f, indent=2)
        print(
            f"Fallback review.json saved in {time.time() - start_time:.2f} seconds to {os.getcwd()}")


def main():
    print("Starting main...")
    start_time = time.time()
    try:
        if len(sys.argv) > 1 and sys.argv[1] == '--github':
            diff = os.environ.get('PR_DIFF', '')
            print("Using PR_DIFF from GitHub Actions")
        else:
            try:
                repo = git.Repo('.')
                diff = repo.git.diff('main')
                print("Using local git diff")
            except Exception as e:
                print(f"Error getting diff: {str(e)}")
                save_review_results([{
                    "file": "unknown",
                    "line": 1,
                    "snippet": "",
                    "comment": f"Error getting diff: {str(e)}\n\n**Resolve:** Mark as resolved in GitHub UI"
                }])
                return
    except Exception as e:
        print(f"Error in main: {str(e)}")
        save_review_results([{
            "file": "unknown",
            "line": 1,
            "snippet": "",
            "comment": f"Error in main: {str(e)}\n\n**Resolve:** Mark as resolved in GitHub UI"
        }])
        return

    if not diff:
        print("No changes detected.")
        save_review_results([{
            "file": "unknown",
            "line": 1,
            "snippet": "",
            "comment": "No changes detected.\n\n**Resolve:** Mark as resolved in GitHub UI"
        }])
        return

    results = review_code(diff)
    save_review_results(results)
    print(json.dumps(results, indent=2))
    print(f"Main completed in {time.time() - start_time:.2f} seconds")


if __name__ == '__main__':
    main()
