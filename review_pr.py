import os
import json
import re
from subprocess import run, PIPE
import py_compile  # For Python syntax checks

# Assuming parse_unified_diff and rule_based_review are defined elsewhere


def run_syntax_checks(file_path: str):
    """Run language-specific syntax/lint checks and return issues."""
    results = []
    ext = os.path.splitext(file_path)[1]

    try:
        if ext in (".js", ".jsx", ".ts", ".tsx"):
            # Use ESLint if available
            p = run(["eslint", "-f", "json", file_path],
                    stdout=PIPE, stderr=PIPE, text=True)
            if p.returncode != 0 or p.stdout.strip():  # Check returncode too
                try:
                    eslint_output = json.loads(p.stdout)
                    for msg in eslint_output[0].get("messages", []):
                        results.append({
                            "file": file_path,
                            "line": int(msg.get("line", 1)),  # Ensure int
                            "comment": msg.get("message", "Lint issue")
                        })
                except json.JSONDecodeError:
                    # Fallback if ESLint output isn't valid JSON
                    results.append({
                        "file": file_path,
                        "line": 1,
                        "comment": f"ESLint failed: {p.stderr.strip()}"
                    })

        elif ext == ".py":
            try:
                py_compile.compile(file_path, doraise=True)
            except py_compile.PyCompileError as e:
                # Extract line from error if possible
                line_msg = str(e).split(',')[0] if ',' in str(e) else "1"
                results.append({
                    "file": file_path,
                    "line": int(line_msg.split()[-1]) if line_msg.isdigit() else 1,
                    "comment": f"Python syntax error: {str(e)}"
                })
            except Exception as py_e:  # Catch other compile issues
                results.append({
                    "file": file_path,
                    "line": 1,
                    "comment": f"Python compile failed: {str(py_e)}"
                })

    except Exception as e:
        # Log for debugging
        print(f"Syntax check error for {file_path}: {str(e)}")
        results.append({
            "file": file_path,
            "line": 1,
            "comment": f"Syntax check failed: {str(e)}"
        })

    return results


def safe_extract_json(text: str):
    """Extract and parse JSON array reliably from model output."""
    text = text.strip()  # Remove leading/trailing whitespace
    try:
        return json.loads(text)
    except Exception:
        pass

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


def review_code(diff):
    if not diff or diff.startswith("Error getting diff:"):
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
            continue

        prompt = (
            "You are an expert software code reviewer. Review the following file for:\n"
            "- Syntax errors\n"
            "- Logical bugs (boundary conditions, off-by-one, wrong variables)\n"
            "- Performance issues\n"
            "- Security concerns (secrets, injection, unsafe code)\n"
            "- Maintainability and readability\n\n"
            f"File: {file_path}\n\n"
            f"```{full_code}```\n\n"
            "Return ONLY a valid JSON array with no extra text. Each object: "
            '{"file": "relative/path.js", "line": <line_number>, "comment": "specific suggestion"}'
        )

        try:
            from ollama import Client  # Assuming ollama is installed
            client = Client(host='http://127.0.0.1:11434')
            response = client.generate(
                model='codellama:7b-instruct', prompt=prompt)
            raw_text = response.get("response", "").strip()

            items = safe_extract_json(raw_text)

            # Ensure items are dicts with int lines
            for item in items:
                if isinstance(item.get("line"), str):
                    try:
                        item["line"] = int(item["line"])
                    except ValueError:
                        item["line"] = 1

            # Filter only lines belonging to this file (LLM might over-report)
            filtered = [i for i in items if i.get("file") == file_path]
            if filtered:  # Only extend if not empty
                final_results.extend(filtered)
            else:
                # Debug log
                print(
                    f"LLM produced empty/ invalid JSON for {file_path}: {raw_text[:200]}...")

        except Exception as e:
            # Log for debugging
            print(f"LLM review failed for {file_path}: {str(e)}")
            final_results.append({
                "file": file_path,
                "line": 1,
                "comment": f"AI review failed: {str(e)}"
            })

    save_review_results(final_results)
    return final_results
