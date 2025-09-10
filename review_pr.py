def review_code(diff):
    print("Starting code review...")
    start_time = time.time()
    if not diff or diff.startswith("Error"):
        print("No valid diff provided.")
        return [{
            "file": "unknown",
            "line": 1,
            "comment": "No valid diff provided.\n\n**Resolve:** Mark as resolved in GitHub UI"
        }]

    final_results = []
    added_lines_by_file, added_text_by_file = parse_unified_diff(diff)

    if not added_lines_by_file:
        print("No files with changes detected in diff.")
        final_results.append({
            "file": "unknown",
            "line": 1,
            "comment": "No files with changes detected.\n\n**Resolve:** Mark as resolved in GitHub UI"
        })
        print(
            f"Code review completed in {time.time() - start_time:.2f} seconds")
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
                    item["snippet"] = snippet  # Store snippet separately
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

    print(f"Code review completed in {time.time() - start_time:.2f} seconds")
    return final_results
