import os
import requests
import json
from flask import Flask, render_template

app = Flask(__name__)

REGISTRY_URL = os.environ.get('REGISTRY_URL', 'http://localhost:5000').rstrip('/')

def get_repositories():
    try:
        response = requests.get(f"{REGISTRY_URL}/v2/_catalog", timeout=5)
        response.raise_for_status()
        return response.json().get('repositories', [])
    except Exception as e:
        print(f"Error fetching repositories: {e}")
        return []

def get_tags(repo):
    try:
        response = requests.get(f"{REGISTRY_URL}/v2/{repo}/tags/list", timeout=5)
        response.raise_for_status()
        return response.json().get('tags', [])
    except Exception as e:
        print(f"Error fetching tags for {repo}: {e}")
        return []

def get_manifest(repo, tag):
    try:
        headers = {'Accept': 'application/vnd.docker.distribution.manifest.v2+json'}
        response = requests.get(f"{REGISTRY_URL}/v2/{repo}/manifests/{tag}", headers=headers, timeout=5)
        response.raise_for_status()
        return response.json(), response.headers.get('Docker-Content-Digest')
    except Exception as e:
        print(f"Error fetching manifest for {repo}:{tag}: {e}")
        return None, None

def get_blob(repo, digest):
    try:
        response = requests.get(f"{REGISTRY_URL}/v2/{repo}/blobs/{digest}", timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching blob {digest} for {repo}: {e}")
        return None

def is_sha(tag):
    # A simple heuristic for git sha: hex string, typically 7, 8 or 40 chars
    if len(tag) in (7, 8, 40) and all(c in '0123456789abcdefABCDEF' for c in tag):
        return True
    return False

def get_registry_data():
    repos = get_repositories()

    # We want to group by identical "builds".
    # A build can be identified by the tags that point to the exact same image content.
    # However, across *different* repositories (e.g. api, ui), the image digests will be different.
    # But they share the SAME tag strings (e.g., pushed with `1.0.0` and `abcdef1`).
    # So we should group by the *set of tags* that were pushed together.

    # Actually, a simpler way:
    # We find all tags across all repos.
    # A user clicks a tag (or SHA), and we show all repos that have that tag/sha.
    # But the user asked for a sidebar with:
    # Tags: v1, v2, v3
    # Commit: 03de492

    # To do this, we need to correlate which tags and SHAs go together.
    # If the `api` image is tagged with `v1` and `03de492`, its manifest for `v1` will have the same digest as its manifest for `03de492`.
    # This proves `v1` == `03de492`.

    # Let's map out: repo -> digest -> list of tags
    repo_digest_tags = {}

    for repo in repos:
        repo_digest_tags[repo] = {}
        tags = get_tags(repo)
        if not tags:
            continue

        for tag in tags:
            manifest, digest = get_manifest(repo, tag)
            if not manifest or not digest:
                continue

            if digest not in repo_digest_tags[repo]:
                # Fetch config blob for date and size
                config_digest = manifest.get('config', {}).get('digest')
                created_date = "Unknown"
                size = 0

                # sum up layer sizes
                for layer in manifest.get('layers', []):
                    size += layer.get('size', 0)

                if config_digest:
                    config_data = get_blob(repo, config_digest)
                    if config_data:
                        created_date = config_data.get('created', "Unknown")

                repo_digest_tags[repo][digest] = {
                    'tags': [],
                    'created': created_date,
                    'size': size
                }

            repo_digest_tags[repo][digest]['tags'].append(tag)

    # Now we need to group these cross-repo into "Builds" for the sidebar.
    # A "build" is defined by a set of tags (e.g., ['v1', '03de492']).
    # If repo 'api' has digest D1 with tags ['v1', '03de492']
    # and repo 'ui' has digest D2 with tags ['v1', '03de492']
    # They belong to the same logical build.

    builds_map = {} # tag_set_tuple -> { 'tags': [], 'sha': '', 'images': [] }

    for repo, digests in repo_digest_tags.items():
        for digest, data in digests.items():
            tag_list = sorted(data['tags'])
            tag_tuple = tuple(tag_list)

            if tag_tuple not in builds_map:
                shas = [t for t in tag_list if is_sha(t)]
                normal_tags = [t for t in tag_list if not is_sha(t)]

                # If there are no normal tags, maybe it's just a SHA push.
                # We'll just display them as best as we can.
                sha_display = shas[0] if shas else ""
                tags_display = ", ".join(normal_tags) if normal_tags else (", ".join(shas) if len(shas) > 1 else "")

                # if there is no normal tag and only 1 sha, we just show sha.
                if not normal_tags and shas:
                    tags_display = "No Tags"

                builds_map[tag_tuple] = {
                    'id': "-".join(tag_list), # unique id for html
                    'sha': sha_display,
                    'tags_str': tags_display,
                    'images': []
                }

            # Add this image to the build
            # Size is in bytes, convert to MB
            size_mb = round(data['size'] / (1024 * 1024), 2)

            # format date string a bit nicer if it's ISO
            date_str = data['created']
            if 'T' in date_str:
                date_str = date_str.split('T')[0] + " " + date_str.split('T')[1][:8]

            builds_map[tag_tuple]['images'].append({
                'repo': repo,
                'size_mb': size_mb,
                'created': date_str,
                'digest': digest
            })

    # Convert builds map to a list sorted by date (latest first based on images inside)
    builds = list(builds_map.values())

    def get_latest_date(build):
        dates = [img['created'] for img in build['images'] if img['created'] != "Unknown"]
        return max(dates) if dates else ""

    builds.sort(key=get_latest_date, reverse=True)

    return builds

@app.route('/')
def index():
    builds = get_registry_data()
    return render_template('index.html', builds=builds)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
