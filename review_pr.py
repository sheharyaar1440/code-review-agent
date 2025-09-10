import os
import json
import re
import sys
from subprocess import run, PIPE
import py_compile
import git
import time
try:
    from ollama import Client
    try:
        from ollama import OllamaError
    except ImportError:
        OllamaError = Exception
        print("Warning: OllamaError not found, using generic Exception as fallback")
except ImportError:
    print("Error: ollama package not found. Please install it with 'pip install ollama'")
    sys.exit(1)


def parse_unified_diff(diff):
    print("Starting diff parsing...")
    start_time = time.time()
    added_lines_by_file = {}
    added_text_by_file = {}
    file_changes = {}  # Track all changes per file
    current_file = None
    current_lines = []
    current_text = []
    current_line = None

    try:
        if len(diff) > 500000:  # Increased limit for larger diffs
            print("Diff too large, truncating...")
            diff = diff[:500000]

        print(
            f"Diff content (first 20 lines):\n{chr(10).join(diff.split(chr(10))[:20])}")

        for line in diff.split('\n'):
            if line.startswith('diff --git'):
                # Save previous file changes
                if current_file and current_lines:
                    added_lines_by_file[current_file] = current_lines.copy()
                    added_text_by_file[current_file] = '\n'.join(current_text)

                # Parse new file path - handle both a/ and b/ prefixes
                parts = line.split()
                if len(parts) >= 4:
                    # Extract from "diff --git a/path b/path"
                    b_file = parts[3]
                    if b_file.startswith('b/'):
                        current_file = b_file[2:]
                    else:
                        current_file = b_file
                else:
                    current_file = None

                current_lines = []
                current_text = []
                current_line = None

            elif line.startswith('+++'):
                # Alternative way to get file path
                if not current_file and 'b/' in line:
                    current_file = line.split('b/')[-1].strip()

            elif line.startswith('@@') and current_file:
                # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
                match = re.match(r'@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@', line)
                if match:
                    new_start = int(match.group(3))
                    current_line = new_start
                    print(
                        f"Found hunk for {current_file} starting at line {current_line}")

            elif current_file and current_line is not None:
                if line.startswith('+') and not line.startswith('+++'):
                    # This is an added line
                    current_lines.append(current_line)
                    current_text.append(line[1:])  # Remove the '+' prefix
                    print(
                        f"Added line {current_line} in {current_file}: {line[1:].strip()[:50]}...")
                    current_line += 1
                elif line.startswith(' '):
                    # Context line - increment line number but don't save
                    current_line += 1
                elif line.startswith('-'):
                    # Deleted line - don't increment new line number
                    pass

        # Save the last file
        if current_file and current_lines:
            added_lines_by_file[current_file] = current_lines
            added_text_by_file[current_file] = '\n'.join(current_text)

    except Exception as e:
        print(f"Error parsing diff: {str(e)}")
        import traceback
        traceback.print_exc()
        return {}, {}

    print(f"Diff parsing completed in {time.time() - start_time:.2f} seconds")
    print(f"Found changes in {len(added_lines_by_file)} files:")
    for file_path, lines in added_lines_by_file.items():
        print(f"  {file_path}: {len(lines)} added lines")

    return added_lines_by_file, added_text_by_file


def rule_based_review(file_path, added_lines):
    """Run rule-based checks on specific lines"""
    results = []

    if not os.path.exists(file_path) or not added_lines:
        return results

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        ext = os.path.splitext(file_path)[1]

        for line_num in added_lines:
            if line_num <= len(lines):
                line_content = lines[line_num - 1].strip()
                line_lower = line_content.lower()

                # JavaScript/TypeScript specific checks
                if ext in ('.js', '.jsx', '.ts', '.tsx'):
                    # Check for console.log in production code
                    if 'console.log' in line_content and 'src/' in file_path:
                        results.append({
                            "file": file_path,
                            "line": line_num,
                            "snippet": "",
                            "comment": "**Debug Code Detected**\n\n`console.log` statements should be removed before production.\n\nConsider using a proper logging library instead.\n\n---\n*Remove debug code before merging.*"
                        })

                    # Check for var usage
                    if line_content.strip().startswith('var '):
                        results.append({
                            "file": file_path,
                            "line": line_num,
                            "snippet": "",
                            "comment": "**Use Modern JavaScript**\n\nPrefer `const` or `let` instead of `var` for better scoping.\n\n```javascript\nconst value = ...;\n// or\nlet value = ...;\n```"
                        })

                    # Check for == instead of ===
                    if ' == ' in line_content and '===' not in line_content:
                        results.append({
                            "file": file_path,
                            "line": line_num,
                            "snippet": "",
                            "comment": "**Use Strict Equality**\n\nUse `===` instead of `==` for strict equality comparison.\n\n```javascript\nif (value === expected) {\n  // strict comparison\n}\n```"
                        })

                # Python specific checks
                elif ext == '.py':
                    # Check for print statements (potential debug code)
                    if line_content.strip().startswith('print(') and 'test' not in file_path.lower():
                        results.append({
                            "file": file_path,
                            "line": line_num,
                            "snippet": "",
                            "comment": "**Debug Code Detected**\n\n`print()` statements should typically be replaced with proper logging.\n\n```python\nimport logging\nlogging.info('Your message here')\n```"
                        })

                    # Check for bare except clauses
                    if line_content.strip() == 'except:':
                        results.append({
                            "file": file_path,
                            "line": line_num,
                            "snippet": "",
                            "comment": "**Avoid Bare Except**\n\nBare `except:` clauses catch all exceptions, including system exits.\n\n```python\ntry:\n    # code\nexcept SpecificException as e:\n    # handle specific exception\n```"
                        })

                # General checks for all files
                # Check for TODO comments
                if 'todo' in line_lower or 'fixme' in line_lower:
                    results.append({
                        "file": file_path,
                        "line": line_num,
                        "snippet": "",
                        "comment": "**TODO/FIXME Found**\n\nConsider creating a GitHub issue to track this work item instead of leaving it in comments."
                    })

                # Check for very long lines
                if len(line_content) > 120:
                    results.append({
                        "file": file_path,
                        "line": line_num,
                        "snippet": "",
                        "comment": "**Line Too Long**\n\nThis line is over 120 characters. Consider breaking it into multiple lines for better readability."
                    })

    except Exception as e:
        print(f"Rule-based review error for {file_path}: {str(e)}")

    return results


def run_syntax_checks(file_path: str, added_lines: list):
    print(f"Running syntax checks for {file_path}...")
    start_time = time.time()
    results = []
    ext = os.path.splitext(file_path)[1]

    # Skip syntax checks if file doesn't exist
    if not os.path.exists(file_path):
        print(f"File {file_path} does not exist, skipping syntax checks")
        return results

    try:
        if ext in (".js", ".jsx", ".ts", ".tsx"):
            # Try to find eslint in node_modules or global
            eslint_cmd = None
            for cmd in ["npx eslint", "eslint", "./node_modules/.bin/eslint"]:
                try:
                    p = run(cmd.split() + ["--version"],
                            stdout=PIPE, stderr=PIPE, timeout=5)
                    if p.returncode == 0:
                        eslint_cmd = cmd.split()
                        break
                except:
                    continue

            if eslint_cmd:
                p = run(eslint_cmd + ["-f", "json", file_path],
                        stdout=PIPE, stderr=PIPE, text=True, timeout=30)
                if p.stdout.strip():
                    try:
                        eslint_output = json.loads(p.stdout)
                        for file_result in eslint_output:
                            for msg in file_result.get("messages", []):
                                line_num = int(msg.get("line", 1))
                                # Only report issues on lines that were actually changed
                                if line_num in added_lines:
                                    results.append({
                                        "file": file_path,
                                        "line": line_num,
                                        "snippet": "",
                                        "comment": f"**ESLint Issue**\n\n{msg.get('message', 'Lint issue')}\n\n**Rule:** `{msg.get('ruleId', 'unknown')}`\n\n---\n*Please fix this linting issue.*"
                                    })
                    except json.JSONDecodeError:
                        pass
            else:
                print(
                    f"ESLint not found, skipping JS/TS checks for {file_path}")

        elif ext == ".py":
            try:
                py_compile.compile(file_path, doraise=True)
            except py_compile.PyCompileError as e:
                error_str = str(e)
                # Extract line number from Python compile error
                import re
                line_match = re.search(r'line (\d+)', error_str)
                if line_match:
                    line_num = int(line_match.group(1))
                    # Only report if the error is on a changed line
                    if line_num in added_lines:
                        results.append({
                            "file": file_path,
                            "line": line_num,
                            "snippet": "",
                            "comment": f"**Python Syntax Error**\n\n{str(e)}\n\n---\n*Please fix this syntax error before proceeding.*"
                        })
            except Exception as py_e:
                # Generic Python error, put on first changed line
                if added_lines:
                    results.append({
                        "file": file_path,
                        "line": added_lines[0],
                        "snippet": "",
                        "comment": f"**Python Error:** {str(py_e)}"
                    })
    except Exception as e:
        print(f"Syntax check error for {file_path}: {str(e)}")
        # Don't add generic errors that can't be tied to specific lines

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

    if not diff or not file_path:
        return ""

    lines = diff.split('\n')
    snippet = []
    current_line = None
    in_target_file = False
    context_window = 3  # Lines of context before and after

    try:
        for i, line in enumerate(lines):
            # Check if we're starting a new file section
            if line.startswith('diff --git'):
                # Check if this is our target file
                if file_path in line:
                    in_target_file = True
                else:
                    in_target_file = False
                continue

            # Skip if we're not in the target file
            if not in_target_file:
                continue

            # Parse hunk headers
            if line.startswith('@@'):
                match = re.match(r'@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@', line)
                if match:
                    new_start = int(match.group(3))
                    current_line = new_start
                continue

            # Process diff lines
            if current_line is not None and line.startswith(('+', '-', ' ')):
                # Check if this line is within our context window
                if abs(current_line - line_number) <= context_window:
                    snippet.append(line)

                # If this is an added line, increment the line number
                if line.startswith('+') and not line.startswith('+++'):
                    if current_line == line_number:
                        # Found our target line, we can return the snippet
                        result = '\n'.join(snippet)
                        print(
                            f"Snippet extraction completed in {time.time() - start_time:.2f} seconds")
                        return result
                    current_line += 1
                elif line.startswith(' '):
                    # Context line
                    current_line += 1
                # Note: deleted lines (starting with '-') don't increment the new line number

    except Exception as e:
        print(
            f"Error extracting snippet for {file_path}:{line_number}: {str(e)}")
        import traceback
        traceback.print_exc()
        return ""

    # If we didn't find the exact line, return what we have
    result = '\n'.join(snippet) if snippet else ""
    print(
        f"Snippet extraction completed in {time.time() - start_time:.2f} seconds")
    return result


def should_skip_file(file_path):
    """Check if a file should be skipped from review"""
    if not file_path:
        return True

    # Skip review system files
    excluded_files = {
        'review_pr.py',
        'review.py',
        '.github/workflows/pr-review.yml',
        'pr-review.yml'
    }

    # Skip certain directories and file types
    excluded_patterns = [
        '.github/',
        'node_modules/',
        '__pycache__/',
        '.git/',
        '.vscode/',
        '.idea/',
        'dist/',
        'build/',
        'coverage/',
        '.nyc_output/',
        'package-lock.json',
        'yarn.lock',
        '.env',
        '.env.local',
        '.env.production'
    ]

    # Check exact file matches
    if file_path in excluded_files:
        return True

    # Check pattern matches
    for pattern in excluded_patterns:
        if file_path.startswith(pattern) or pattern in file_path:
            return True

    return False


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
            "comment": "No changes detected.\n\n**Resolve:** Mark as resolved in GitHub UI"
        })
        print(
            f"Code review completed in {time.time() - start_time:.2f} seconds")
        save_review_results(final_results)
        return final_results

    # Filter out excluded files
    reviewable_files = {
        k: v for k, v in added_lines_by_file.items() if not should_skip_file(k)}

    if not reviewable_files:
        print("No reviewable files found after filtering.")
        final_results.append({
            "file": "excluded",
            "line": 1,
            "snippet": "",
            "comment": "Only excluded files were changed (review system files, configs, etc.)\n\n**Resolve:** Mark as resolved in GitHub UI"
        })
        save_review_results(final_results)
        return final_results

    print(
        f"Reviewing {len(reviewable_files)} files: {list(reviewable_files.keys())}")

    # Test Ollama connection early to avoid timeouts later
    ollama_available = False
    try:
        print("Testing Ollama connection...")
        client = Client(host='http://127.0.0.1:11434')
        test_response = client.generate(
            model='codellama:7b-instruct',
            prompt='Hello',
            options={'timeout': 10, 'num_predict': 5}
        )
        ollama_available = True
        print("✓ Ollama connection successful!")
    except Exception as e:
        print(f"✗ Ollama connection failed: {str(e)}")
        print("Continuing with syntax checks only...")

    for file_path, added_lines in reviewable_files.items():
        print(f"Reviewing file: {file_path}")
        file_start_time = time.time()

        # Skip file if we've been running for too long (prevent overall timeout)
        if time.time() - start_time > 240:  # 4 minutes total limit
            print(f"Skipping {file_path}: overall timeout approaching")
            final_results.append({
                "file": file_path,
                "line": 1,
                "snippet": "",
                "comment": "Review timeout - please check this file manually\n\n**Resolve:** Mark as resolved in GitHub UI"
            })
            continue

        # 1️⃣ Run syntax/lint checks (quick)
        try:
            syntax_items = run_syntax_checks(file_path, added_lines)
            final_results.extend(syntax_items)
        except Exception as e:
            print(f"Syntax check failed for {file_path}: {str(e)}")

        # 2️⃣ Run rule-based checks (quick)
        try:
            rule_based_items = rule_based_review(file_path, added_lines)
            final_results.extend(rule_based_items)
        except Exception as e:
            print(f"Rule-based check failed for {file_path}: {str(e)}")

        # 3️⃣ Run LLM review on changed lines only
        if not ollama_available:
            print(f"Skipping AI review for {file_path}: Ollama not available")
            continue

        added_text = added_text_by_file.get(file_path, "")
        if not added_text.strip():
            print(f"No added content found for {file_path}")
            continue

        try:
            # Get context around changed lines for better review
            print(f"Reading file {file_path}...")
            with open(file_path, "r", encoding="utf-8") as f:
                file_lines = f.readlines()

            # Create focused content for review (changed lines + context)
            review_content = []
            for line_num in added_lines:
                # Add context lines around each changed line
                start_line = max(0, line_num - 5)
                end_line = min(len(file_lines), line_num + 5)

                for i in range(start_line, end_line):
                    line_content = file_lines[i].rstrip()
                    marker = ">>> NEW" if (i + 1) in added_lines else "    "
                    review_content.append(f"{i+1:4d} {marker} {line_content}")

            focused_content = '\n'.join(review_content)

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
            f"You are an expert {language} code reviewer. Review ONLY the lines marked with '>>> NEW' in the following code.\n"
            f"Focus on these specific concerns for the NEW lines:\n"
            "- Syntax errors and typos\n"
            "- Logic bugs and potential runtime errors\n"
            "- Security vulnerabilities\n"
            "- Performance issues\n"
            "- Code quality and best practices\n\n"
            f"File: {file_path}\n"
            f"Lines to review: {added_lines}\n\n"
            f"Code with context (focus on lines marked '>>> NEW'):\n"
            f"```{language.lower()}\n{focused_content}\n```\n\n"
            "Return ONLY a valid JSON array with no markdown or extra text. For each issue found on NEW lines, create an object with:\n"
            '{"file": "' + file_path +
            '", "line": <actual_line_number>, "snippet": "", "comment": "Brief, specific suggestion for improvement"}\n'
            "Only include issues for lines marked '>>> NEW'. Respond with [] if no issues found."
        )

        try:
            print(f"Connecting to Ollama for {file_path}...")

            # Skip AI review if too many lines to avoid timeout
            if len(added_lines) > 20:
                print(
                    f"Skipping AI review for {file_path}: too many changes ({len(added_lines)} lines)")
                final_results.append({
                    "file": file_path,
                    "line": added_lines[0] if added_lines else 1,
                    "snippet": "",
                    "comment": f"Large changeset detected ({len(added_lines)} lines). Please review manually for complex logic, security issues, and performance concerns.\n\n**Resolve:** Mark as resolved in GitHub UI"
                })
                continue

            # Limit prompt size to prevent timeout
            if len(prompt) > 8000:
                print(f"Prompt too large for {file_path}, truncating...")
                prompt = prompt[:8000] + \
                    "\n\nNote: Content truncated due to size. Focus on critical issues."

            client = Client(host='http://127.0.0.1:11434')

            # Use shorter timeout and optimized options
            response = client.generate(
                model='codellama:7b-instruct',
                prompt=prompt,
                options={
                    'timeout': 45,  # Reduced from 60
                    'temperature': 0.1,  # Lower temperature for more focused responses
                    'top_p': 0.9,
                    'num_predict': 512,  # Limit response length
                }
            )
            raw_text = response.get("response", "").strip()
            print(f"Raw LLM output for {file_path}: {raw_text[:150]}...")

            items = safe_extract_json(raw_text)
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue

                    # Ensure line number is an integer
                    line_num = item.get("line")
                    if isinstance(line_num, str):
                        try:
                            line_num = int(line_num)
                        except ValueError:
                            continue
                    elif not isinstance(line_num, int):
                        continue

                    # Only include comments for lines that were actually added
                    if line_num in added_lines:
                        # Extract relevant snippet from the diff
                        snippet = extract_snippet(diff, line_num, file_path)

                        # Create a well-formatted review comment
                        comment = item.get("comment", "").strip()
                        if comment:
                            # Format AI comments consistently
                            formatted_comment = f"**AI Code Review**\n\n{comment}\n\n---\n*This comment was generated by AI. Please review and mark as resolved if addressed.*"
                            final_results.append({
                                "file": file_path,
                                "line": line_num,
                                "snippet": snippet,
                                "comment": formatted_comment
                            })
                            print(
                                f"Added AI review comment for {file_path}:{line_num}")

            if not items:
                print(
                    f"LLM produced empty/invalid JSON for {file_path}: {raw_text[:200]}...")
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

        # Check if we've spent too much time on this file
        file_duration = time.time() - file_start_time
        if file_duration > 60:  # 1 minute per file max
            print(
                f"File {file_path} took {file_duration:.1f}s - consider optimization")

    # Ensure we always have some results
    if not final_results:
        final_results.append({
            "file": "review_completed",
            "line": 1,
            "snippet": "",
            "comment": "Code review completed successfully. No issues found in the changed code.\n\n**Resolve:** Mark as resolved in GitHub UI"
        })

    print(
        f"Code review completed in {time.time() - start_time:.2f} seconds with {len(final_results)} comments")
    save_review_results(final_results)
    return final_results


def save_review_results(results):
    print("Saving review.json...")
    start_time = time.time()
    try:
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
    diff = ""

    try:
        if len(sys.argv) > 1 and sys.argv[1] == '--github':
            # Running in GitHub Actions
            diff = os.environ.get('PR_DIFF', '')
            print(
                f"PR_DIFF from environment: {len(diff) if diff else 0} characters")

            if not diff:
                print("PR_DIFF is empty, attempting to fetch diff directly...")
                try:
                    repo = git.Repo('.')
                    # Try different diff approaches
                    diff = repo.git.diff('origin/main...HEAD')
                    if not diff:
                        diff = repo.git.diff('HEAD~1')
                    print(f"Fetched diff: {len(diff)} characters")
                except Exception as git_e:
                    print(f"Git diff failed: {str(git_e)}")
        else:
            # Running locally
            print("Running in local mode...")
            try:
                repo = git.Repo('.')
                # For local development, compare against main
                diff = repo.git.diff('main')
                if not diff:
                    diff = repo.git.diff('HEAD~1')
                print(f"Local diff: {len(diff)} characters")
            except Exception as e:
                print(f"Error getting local diff: {str(e)}")
                save_review_results([{
                    "file": "unknown",
                    "line": 1,
                    "snippet": "",
                    "comment": f"Error getting diff: {str(e)}\n\n**Resolve:** Mark as resolved in GitHub UI"
                }])
                return

    except Exception as e:
        print(f"Error in main setup: {str(e)}")
        save_review_results([{
            "file": "unknown",
            "line": 1,
            "snippet": "",
            "comment": f"Error in main: {str(e)}\n\n**Resolve:** Mark as resolved in GitHub UI"
        }])
        return

    if not diff or len(diff.strip()) == 0:
        print("No changes detected in diff.")
        save_review_results([{
            "file": "No changes",
            "line": 1,
            "snippet": "",
            "comment": "No changes detected in this PR.\n\n**Resolve:** Mark as resolved in GitHub UI"
        }])
        return

    print(f"Processing diff with {len(diff)} characters...")
    results = review_code(diff)

    print(f"Review completed with {len(results)} comments")
    for result in results:
        print(
            f"  - {result.get('file', 'unknown')}:{result.get('line', 1)} - {result.get('comment', '')[:100]}...")

    # Additional debugging info
    print(f"\nDetailed review results:")
    for i, result in enumerate(results):
        print(f"Comment {i+1}:")
        print(f"  File: {result.get('file', 'N/A')}")
        print(f"  Line: {result.get('line', 'N/A')}")
        print(f"  Comment length: {len(result.get('comment', ''))}")
        print(f"  Has snippet: {bool(result.get('snippet', ''))}")
        print("---")

    save_review_results(results)
    print(json.dumps(results, indent=2))
    print(f"Main completed in {time.time() - start_time:.2f} seconds")


if __name__ == '__main__':
    main()
