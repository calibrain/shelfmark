#!/usr/bin/env python3
"""
Sync your Hardcover "Want to Read" list to Shelfmark's request queue.

This script fetches books from your Hardcover wishlist (status_id: 1) and adds
them to the Shelfmark SQLite database (`download_requests` table) if they aren't
already requested or downloaded.

Usage:
    python scripts/sync_hardcover_wishlist.py --token <your_hardcover_token> --db <path_to_users.db>

Alternatively, set the environment variables:
    export HARDCOVER_TOKEN="your_hardcover_token"
    export SHELFMARK_DB="/config/users.db"
    python scripts/sync_hardcover_wishlist.py
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from typing import Any


def fetch_hardcover_wishlist(token: str, limit: int = 100) -> list[dict[str, Any]]:
    """Fetch all books on the 'Want to Read' shelf (status_id: 1) from Hardcover."""
    query = """
    query getUserBooks($offset: Int, $limit: Int) {
      me {
        user_books(
          offset: $offset,
          limit: $limit,
          where: {status_id: {_eq: 1}}
        ) {
          book {
            id
            title
            contributions(where: {contributable_type: {_eq: "Book"}}) {
              author {
                name
              }
            }
          }
        }
      }
    }
    """
    
    books: list[dict[str, Any]] = []
    offset = 0
    
    while True:
        req_data = json.dumps({
            "query": query,
            "variables": {"offset": offset, "limit": limit}
        }).encode('utf-8')

        req = urllib.request.Request(
            "https://api.hardcover.app/v1/graphql",
            data=req_data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
        )
        try:
            with urllib.request.urlopen(req) as response:
                res = json.loads(response.read().decode('utf-8'))
                
                # Check for GraphQL errors
                if "errors" in res:
                    print(f"GraphQL Errors: {res['errors']}", file=sys.stderr)
                    break
                    
                me_data = res.get("data", {}).get("me", [])
                if not me_data:
                    break
                    
                user_books = me_data[0].get("user_books", [])
                if not user_books:
                    break
                    
                for ub in user_books:
                    book_info = ub.get("book")
                    if book_info:
                        # Extract primary author
                        authors = [
                            c.get("author", {}).get("name") 
                            for c in book_info.get("contributions", [])
                        ]
                        authors = [a for a in authors if a]
                        author = authors[0] if authors else "Unknown"
                        
                        books.append({
                            "id": book_info.get("id"),
                            "title": book_info.get("title"),
                            "author": author
                        })
                        
                offset += limit
                if len(user_books) < limit:
                    break
        except Exception as e:
            print(f"Error fetching from Hardcover API: {e}", file=sys.stderr)
            break
            
    return books


def sync_to_db(db_path: str, books: list[dict[str, Any]], username: str, content_type: str) -> None:
    """Sync list of Hardcover books into Shelfmark's requests queue."""
    if not os.path.exists(db_path):
        print(f"Error: Database file not found at {db_path}", file=sys.stderr)
        sys.exit(1)
        
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    try:
        # 1. Ensure a user exists in Shelfmark users table
        cur.execute("SELECT id FROM users WHERE username = ? LIMIT 1;", (username,))
        user_row = cur.fetchone()
        
        if not user_row:
            # Check if there are any users at all
            cur.execute("SELECT id FROM users LIMIT 1;")
            any_user = cur.fetchone()
            if not any_user:
                print(f"No users found in Shelfmark database. Provisioning '{username}' user...")
                cur.execute(
                    "INSERT INTO users (id, username, role, auth_source) VALUES (1, ?, 'admin', 'proxy');",
                    (username,)
                )
                conn.commit()
                user_id = 1
            else:
                user_id = any_user[0]
        else:
            user_id = user_row[0]
            
        # 2. Process book entries
        added_count = 0
        skipped_count = 0
        
        for b in books:
            title = b["title"]
            author = b["author"]
            provider_id = str(b["id"])
            
            # Check if already requested (check book_data JSON fields or provider_id)
            cur.execute(
                "SELECT id FROM download_requests WHERE json_extract(book_data, '$.provider_id') = ?;", 
                (provider_id,)
            )
            if cur.fetchone():
                skipped_count += 1
                continue
                
            # Check download history
            cur.execute(
                "SELECT id FROM download_history WHERE title = ? AND author = ?;", 
                (title, author)
            )
            if cur.fetchone():
                skipped_count += 1
                continue
                
            # Insert request
            book_data = {
                "title": title,
                "author": author,
                "provider": "hardcover",
                "provider_id": provider_id,
                "content_type": content_type
            }
            
            cur.execute(
                """
                INSERT INTO download_requests (
                    user_id, status, delivery_state, content_type, request_level, policy_mode, book_data
                ) VALUES (?, 'pending', 'none', ?, 'book', 'request', ?)
                """,
                (user_id, content_type, json.dumps(book_data))
            )
            print(f"Added request for: '{title}' by {author}")
            added_count += 1
            
        conn.commit()
        print(f"Sync complete. Added {added_count} new requests, skipped {skipped_count} existing.")
    except Exception as e:
        conn.rollback()
        print(f"Database sync failed: {e}", file=sys.stderr)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Sync Hardcover "Want to Read" list to Shelfmark request queue.'
    )
    parser.add_argument(
        '--token',
        default=os.environ.get('HARDCOVER_TOKEN'),
        help='Hardcover Developer API token (env: HARDCOVER_TOKEN)'
    )
    parser.add_argument(
        '--db',
        default=os.environ.get('SHELFMARK_DB', '/config/users.db'),
        help='Path to Shelfmark users.db SQLite file (env: SHELFMARK_DB)'
    )
    parser.add_argument(
        '--username',
        default=os.environ.get('SHELFMARK_USERNAME', 'CalebWest'),
        help='Shelfmark username to associate request with (env: SHELFMARK_USERNAME)'
    )
    parser.add_argument(
        '--content-type',
        default=os.environ.get('CONTENT_TYPE', 'audiobook'),
        choices=['audiobook', 'book'],
        help='Default requested content type: audiobook or book (env: CONTENT_TYPE)'
    )
    
    args = parser.parse_args()
    
    if not args.token:
        print("Error: Hardcover token must be specified via --token or HARDCOVER_TOKEN env var.", file=sys.stderr)
        sys.exit(1)
        
    print(f"Fetching wishlist from Hardcover...")
    wishlist = fetch_hardcover_wishlist(args.token)
    print(f"Found {len(wishlist)} books on your Hardcover 'Want to Read' list.")
    
    if wishlist:
        print(f"Syncing list to Shelfmark database at: {args.db}...")
        sync_to_db(args.db, wishlist, args.username, args.content_type)


if __name__ == '__main__':
    main()
