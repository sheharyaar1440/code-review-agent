import ollama
import git
import os
 import sys
  import re

   def get_pr_diff(repo_path='.'):
        try:
            repo = git.Repo(repo_path)
            current_branch = repo.active_branch.name
            # Adjust 'main' if needed
            diff = repo.git.diff('main', current_branch)
            return diff
        except Exception as e:
            return f"Error getting diff: {str(e)}"

    def extract_snippet(diff, line_number):
        """Extract a code snippet around the given line number from the diff."""
        lines = diff.split('\n')
        snippet = []
        for i, line in enumerate(lines):
            # Look for diff headers like @@ -start,count +start,count @@
            if line.startswith('@@'):
                match = re.match(r'@@ -(\d+),?\d* \+(\d+),?\d* @@', line)
                if match:
                    old_start, new_start = int(
                        match.group(1)), int(match.group(2))
                    current_line = new_start
            elif line.startswith(('+', '-', ' ')) and current_line is not None:
                if abs(current_line - line_number) <= 2:  # Include 2 lines of context
                    snippet.append(line)
                if line.startswith('+'):
                    if current_line == line_number:
                        return '\n'.join(snippet)
                    current_line += 1
                elif line.startswith(' '):
                    current_line += 1
        return '\n'.join(snippet) if snippet else "Snippet not found."

    def review_code(diff):
        if not diff:
            return "No changes to review."
        prompt = f"""
         You are an expert React and JavaScript code reviewer. Review the following code changes (diff) for:
         - JavaScript bugs (e.g., incorrect event handling, state management issues)
         - React best practices (e.g., hooks usage, component structure)
         - Code style (e.g., ESLint rules, consistent formatting)
         - Performance issues (e.g., unnecessary re-renders, large state updates)
         - Security risks (e.g., XSS vulnerabilities, improper prop handling)
         - Suggestions for cleaner, more maintainable code

         Provide comments in a numbered list, with each comment including:
         - The line number (e.g., Line 4)
         - The issue type (e.g., Bug Fix, Code Style, Performance)
         - A detailed description

         Diff:
         {diff}
         """
        try:
            client = ollama.Client(host='http://127.0.0.1:11434')
            response = client.generate(
                model='codellama:7b-instruct', prompt=prompt)
            review = response['response']
            # Parse review and add snippets
            formatted_review = ""
            review_lines = review.split('\n')
            for line in review_lines:
                match = re.match(r'\* Line (\d+): (.*)', line)
                if match:
                    line_number = int(match.group(1))
                    comment = match.group(2)
                    snippet = extract_snippet(diff, line_number)
                    formatted_review += f"**Line {line_number}:**\n"
                    formatted_review += f"```diff\n{snippet}\n```\n"
                    formatted_review += f"**Comment:** {comment}\n"
                    formatted_review += "**Resolve:** Mark as resolved in GitHub UI\n\n"
                else:
                    formatted_review += line + "\n"
            return formatted_review
        except Exception as e:
            return f"Error connecting to Ollama: {str(e)}"

    def main():
        if len(sys.argv) > 1 and sys.argv[1] == '--github':
            diff = os.environ.get('PR_DIFF', get_pr_diff())
        else:
            diff = get_pr_diff()

        if not diff:
            print("No changes detected.")
            return

        review = review_code(diff)
        print("### AI Code Review\n" + review)

    if __name__ == '__main__':
        main()
