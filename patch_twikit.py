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

    # Patch 3: Fix KeyError 'withheld_in_countries' in user.py
    target_withheld = "self.withheld_in_countries: list[str] = legacy['withheld_in_countries']"
    replacement_withheld = "self.withheld_in_countries: list[str] = legacy.get('withheld_in_countries', [])"
    patch_file(user_py, target_withheld, replacement_withheld)

    # Patch 4: Fix KeyError 'pinned_tweet_ids_str' in user.py
    target_pinned = "self.pinned_tweet_ids: list[str] = legacy['pinned_tweet_ids_str']"
    replacement_pinned = "self.pinned_tweet_ids: list[str] = legacy.get('pinned_tweet_ids_str', [])"
    patch_file(user_py, target_pinned, replacement_pinned)

    # Patch 5: Fix cursor parsing bugs in client/client.py
    client_py = twikit_dir / "client" / "client.py"
    if client_py.exists():
        target_cursor1 = "        if entries[-1]['entryId'].startswith('cursor'):\n            next_cursor = entries[-1]['content']['itemContent']['value']"
        replacement_cursor1 = "        if entries[-1]['entryId'].startswith('cursor'):\n            content = entries[-1]['content']\n            next_cursor = content['itemContent']['value'] if 'itemContent' in content else content.get('value')"
        patch_file(client_py, target_cursor1, replacement_cursor1)

        target_cursor2 = "                        if 'cursor' in reply.get('entryId'):\n                            sr_cursor = reply['item']['itemContent']['value']"
        replacement_cursor2 = "                        if 'cursor' in reply.get('entryId'):\n                            item = reply.get('item', {})\n                            item_content = item.get('itemContent', {})\n                            if 'value' in item_content:\n                                sr_cursor = item_content['value']\n                            else:\n                                sr_cursor = item.get('value')"
        patch_file(client_py, target_cursor2, replacement_cursor2)

        target_cursor3 = "        if entries[-1]['entryId'].startswith('cursor'):\n            # if has more replies\n            reply_next_cursor = entries[-1]['content']['itemContent']['value']"
        replacement_cursor3 = "        if entries[-1]['entryId'].startswith('cursor'):\n            # if has more replies\n            content = entries[-1]['content']\n            reply_next_cursor = content['itemContent']['value'] if 'itemContent' in content else content.get('value')"
        patch_file(client_py, target_cursor3, replacement_cursor3)

    # Patch 6: Fix transaction regex in x_client_transaction/transaction.py
    transaction_py = twikit_dir / "x_client_transaction" / "transaction.py"
    if transaction_py.exists():
        # First, apply regular express updates for ondemand loading chunk matching
        content = transaction_py.read_text(encoding="utf-8")
        if 'flags=(re.VERBOSE | re.MULTILINE)' in content:
            print("[+] transaction.py regexes already updated.")
        else:
            simple_target = 'ON_DEMAND_FILE_REGEX = re.compile(\n    r\'\'\',(\\d+):"ondemand\\.s"|\'ondemand\\.s\'\'\'\'\n)'
            simple_replace = 'ON_DEMAND_FILE_REGEX = re.compile(\n    r""",(\\d+):["\']ondemand\\.s["\']""", flags=(re.VERBOSE | re.MULTILINE))'
            if not patch_file(transaction_py, simple_target, simple_replace):
                one_line_target = 'ON_DEMAND_FILE_REGEX = re.compile(r\',(\\d+):"ondemand\\.s"|\'ondemand\\.s\'\')'
                if one_line_target in content:
                    patch_file(transaction_py, one_line_target, 'ON_DEMAND_FILE_REGEX = re.compile(r""",(\\d+):["\']ondemand\\.s["\']""", flags=(re.VERBOSE | re.MULTILINE))')
                else:
                    content = re.sub(
                        r'ON_DEMAND_FILE_REGEX\s*=\s*re\.compile\(.*?\)',
                        'ON_DEMAND_FILE_REGEX = re.compile(r""",(\\d+):["\']ondemand\\.s["\']""", flags=(re.VERBOSE | re.MULTILINE))',
                        content,
                        flags=re.DOTALL
                    )
                    transaction_py.write_text(content, encoding="utf-8")
                    print("[+] Applied regex fallback patch to transaction.py")

        # Now, replace the get_indices method
        target_get_indices = r"""    async def get_indices(self, home_page_response, session, headers):
        key_byte_indices = []
        response = self.validate_response(
            home_page_response) or self.home_page_response
        on_demand_file = ON_DEMAND_FILE_REGEX.search(str(response))
        if on_demand_file:
            on_demand_file_url = f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{on_demand_file.group(1)}a.js"
            on_demand_file_response = await session.request(method="GET", url=on_demand_file_url, headers=headers)
            key_byte_indices_match = INDICES_REGEX.finditer(
                str(on_demand_file_response.text))
            for item in key_byte_indices_match:
                key_byte_indices.append(item.group(2))
        if not key_byte_indices:
            raise Exception("Couldn't get KEY_BYTE indices")
        key_byte_indices = list(map(int, key_byte_indices))
        return key_byte_indices[0], key_byte_indices[1:]"""

        replacement_get_indices = r"""    async def get_indices(self, home_page_response, session, headers):
        key_byte_indices = []
        response = self.validate_response(
            home_page_response) or self.home_page_response
        response_str = str(response)
        
        # 1. Find chunk ID for ondemand.s
        chunk_id_match = re.search(r'(\d+):["\']ondemand\.s["\']', response_str)
        if chunk_id_match:
            chunk_id = chunk_id_match.group(1)
            # 2. Find hash for the chunk ID
            hash_match = re.search(rf'{chunk_id}:["\']([a-f0-9]+)["\']', response_str)
            if hash_match:
                chunk_hash = hash_match.group(1)
                on_demand_file_url = f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{chunk_hash}a.js"
                on_demand_file_response = await session.request(method="GET", url=on_demand_file_url, headers=headers)
                key_byte_indices_match = INDICES_REGEX.finditer(
                    str(on_demand_file_response.text))
                for item in key_byte_indices_match:
                    key_byte_indices.append(item.group(2))
        
        # Fallback to old behavior if new logic failed to find anything
        if not key_byte_indices:
            on_demand_file = ON_DEMAND_FILE_REGEX.search(response_str)
            if on_demand_file:
                on_demand_file_url = f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{on_demand_file.group(1)}a.js"
                on_demand_file_response = await session.request(method="GET", url=on_demand_file_url, headers=headers)
                key_byte_indices_match = INDICES_REGEX.finditer(
                    str(on_demand_file_response.text))
                for item in key_byte_indices_match:
                    key_byte_indices.append(item.group(2))

        if not key_byte_indices:
            raise Exception("Couldn't get KEY_BYTE indices")
        key_byte_indices = list(map(int, key_byte_indices))
        return key_byte_indices[0], key_byte_indices[1:]"""

        patch_file(transaction_py, target_get_indices, replacement_get_indices)
    else:
        print("[-] transaction.py not found in x_client_transaction. Skipping transaction regex patch.")

    print("[+] All twikit patches checked.")

if __name__ == "__main__":
    main()
