"""
patch_twikit.py — Automatically applies necessary patches to the installed twikit package.
Run this script after installing dependencies:
    python patch_twikit.py
"""
import sys
import os
import re
from pathlib import Path

def patch_file(file_path: Path, target_content: str, replacement_content: str) -> bool:
    if not file_path.exists():
        print(f"[-] File not found: {file_path}")
        return False
    try:
        content = file_path.read_text(encoding="utf-8")
        if replacement_content in content:
            print(f"[+] File already patched: {file_path.name}")
            return True
        if target_content in content:
            new_content = content.replace(target_content, replacement_content)
            file_path.write_text(new_content, encoding="utf-8")
            print(f"[+] Successfully patched: {file_path.name}")
            return True
        else:
            # Fallback: check with fuzzy spacing/quotes just in case
            normalized_target = re.sub(r'\s+', '', target_content)
            normalized_content = re.sub(r'\s+', '', content)
            if normalized_target in normalized_content:
                # Target exists but spacing/formatting differs slightly. We will attempt a regex-based replace if needed.
                pass
            print(f"[-] Target content not found in: {file_path.name}")
            return False
    except Exception as e:
        print(f"[-] Error patching {file_path.name}: {e}")
        return False

def main():
    print("Locating twikit package...")
    try:
        import twikit
    except ImportError:
        print("[-] twikit is not installed! Run 'pip install -r requirements.txt' first.")
        sys.exit(1)

    twikit_dir = Path(twikit.__file__).parent
    print(f"[+] Found twikit at: {twikit_dir}")

    # Patch 1: Fix KeyError 'urls' in user.py
    user_py = twikit_dir / "user.py"
    target_user = "self.description_urls: list = legacy['entities']['description']['urls']\n        self.urls: list = legacy['entities'].get('url', {}).get('urls')"
    replacement_user = "self.description_urls: list = legacy['entities'].get('description', {}).get('urls', [])\n        self.urls: list = legacy['entities'].get('url', {}).get('urls', [])"
    patch_file(user_py, target_user, replacement_user)

    # Patch 2: Fix KeyError 'urls' in guest/user.py
    guest_user_py = twikit_dir / "guest" / "user.py"
    patch_file(guest_user_py, target_user, replacement_user)

    # Patch 3: Fix transaction regex in x_client_transaction/transaction.py
    transaction_py = twikit_dir / "x_client_transaction" / "transaction.py"
    if transaction_py.exists():
        target_trans = 'ON_DEMAND_FILE_REGEX = re.compile(\n    r\'\'\',(\\d+):"ondemand\\.s"|\'ondemand\\.s\'\'\'\'\n)'
        replacement_trans = 'ON_DEMAND_FILE_REGEX = re.compile(\n    r\'\'\',(\\d+):["\']ondemand\\.s["\']\'\'\', flags=(re.VERBOSE | re.MULTILINE)\n)'
        
        # We can also do a direct string replace if the formatting is simple:
        content = transaction_py.read_text(encoding="utf-8")
        # Find any variation of ON_DEMAND_FILE_REGEX pattern
        old_pattern = r'ON_DEMAND_FILE_REGEX\s*=\s*re\.compile\(\s*r["\']\'\',\(\\d\+\):"ondemand\\.s"|\'ondemand\\.s\'\'\'["\']\s*\)'
        new_pattern_code = 'ON_DEMAND_FILE_REGEX = re.compile(\n    r""",(\\d+):["\']ondemand\\.s["\']""", flags=(re.VERBOSE | re.MULTILINE))'
        
        if 'flags=(re.VERBOSE | re.MULTILINE)' in content:
            print("[+] transaction.py is already patched.")
        else:
            # Let's perform a direct replace for standard twikit version code:
            simple_target = 'ON_DEMAND_FILE_REGEX = re.compile(\n    r\'\'\',(\\d+):"ondemand\\.s"|\'ondemand\\.s\'\'\'\'\n)'
            simple_replace = 'ON_DEMAND_FILE_REGEX = re.compile(\n    r""",(\\d+):["\']ondemand\\.s["\']""", flags=(re.VERBOSE | re.MULTILINE))'
            
            if not patch_file(transaction_py, simple_target, simple_replace):
                # Try replacing the single-line variant if it exists:
                one_line_target = 'ON_DEMAND_FILE_REGEX = re.compile(r\',(\\d+):"ondemand\\.s"|\'ondemand\\.s\'\')'
                if one_line_target in content:
                    patch_file(transaction_py, one_line_target, 'ON_DEMAND_FILE_REGEX = re.compile(r""",(\\d+):["\']ondemand\\.s["\']""", flags=(re.VERBOSE | re.MULTILINE))')
                else:
                    # Let's do a regex substitution as a fallback
                    content = re.sub(
                        r'ON_DEMAND_FILE_REGEX\s*=\s*re\.compile\(.*?\)',
                        'ON_DEMAND_FILE_REGEX = re.compile(r""",(\\d+):["\']ondemand\\.s["\']""", flags=(re.VERBOSE | re.MULTILINE))',
                        content,
                        flags=re.DOTALL
                    )
                    transaction_py.write_text(content, encoding="utf-8")
                    print("[+] Applied regex fallback patch to transaction.py")
    else:
        print("[-] transaction.py not found in x_client_transaction. Skipping transaction regex patch.")

    print("[+] All twikit patches checked.")

if __name__ == "__main__":
    main()
