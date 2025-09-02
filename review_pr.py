def run_syntax_checks(file_path: str):
    """Run language-specific syntax/lint checks and return issues."""
    results = []
    ext = os.path.splitext(file_path)[1]

    try:
        if ext in (".js", ".jsx", ".ts", ".tsx"):
            # Use ESLint if available
            from subprocess import run, PIPE
            p = run(["eslint", "-f", "json", file_path],
                    stdout=PIPE, stderr=PIPE, text=True)
            if p.stdout.strip():
                eslint_output = json.loads(p.stdout)
                for msg in eslint_output[0].get("messages", []):
                    results.append({
                        "file": file_path,
                        "line": msg.get("line", 1),
                        "comment": msg.get("message", "Lint issue")
                    })

        elif ext == ".py":
            import py_compile
            try:
                py_compile.compile(file_path, doraise=True)
            except py_compile.PyCompileError as e:
                results.append({
                    "file": file_path,
                    "line": 1,
                    "comment": f"Python syntax error: {str(e)}"
                })

        # Add more language integrations here if needed

    except Exception as e:
        results.append({
            "file": file_path,
            "line": 1,
            "comment": f"Syntax check failed: {str(e)}"
        })

    return results


def safe_extract_json(text: str):
    """Extract and parse JSON array reliably from model output."""
    try:
        return json.loads(text)
    except Exception:
        pass

    # Try to isolate first JSON array
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        snippet = text[start:end+1]
        try:
            return json.loads(snippet)
        except Exception:
            # As a last resort, try to fix common JSON issues
            fixed = re.sub(r"(\w+):", r'"\1":', snippet)  # unquoted keys
            fixed = fixed.replace("'", '"')  # single → double quotes
            try:
                return json.loads(fixed)
            except Exception:
                return []
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

        # 3️⃣ Run LLM review (always, to catch logical/style issues)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                full_code = f.read()
        except Exception:
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
            "Return ONLY valid JSON array with objects like:\n"
            '{"file": "relative/path.js", "line": <line_number>, "comment": "specific suggestion"}'
        )

        try:
            client = ollama.Client(host='http://127.0.0.1:11434')
            response = client.generate(
                model='codellama:7b-instruct', prompt=prompt)
            raw_text = response.get("response", "")
            items = safe_extract_json(raw_text)

            items = safe(raw_text)
            # filter only lines belonging to this file (LLM might over-report)
            filtered = [i for i in items if i.get("file") == file_path]
            final_results.extend(filtered)
        except Exception as e:
            final_results.append({
                "file": file_path,
                "line": 1,
                "comment": f"LLM review failed: {str(e)}"
            })

    return final_results
