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

def get_registry_data():
    repos = get_repositories()

    # repo -> digest -> { 'tags': [], 'created': '', 'size': 0 }
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

            # Fetch config blob for date and size
            config_digest = manifest.get('config', {}).get('digest')

            # Use config_digest for grouping if available to correctly group tags sharing the same Image ID
            group_key = config_digest if config_digest else digest

            if group_key not in repo_digest_tags[repo]:
                created_date = "Unknown"
                size = 0

                # sum up layer sizes
                for layer in manifest.get('layers', []):
                    size += layer.get('size', 0)

                if config_digest:
                    config_data = get_blob(repo, config_digest)
                    if config_data:
                        created_date = config_data.get('created', "Unknown")

                repo_digest_tags[repo][group_key] = {
                    'tags': [],
                    'created': created_date,
                    'size': size,
                    # We store the config_digest as the display digest instead of manifest digest,
                    # as this matches `docker images` Image ID. Or we can store the first manifest digest seen.
                    # Using group_key (usually config_digest) is often preferred as it matches Image ID, but we
                    # drop the 'sha256:' prefix when displaying it later.
                    'display_digest': group_key
                }

            # Avoid duplicate tags in case multiple manifests somehow resolve to same config and same tag?
            # Generally tag is unique per repo loop, but safe to just append.
            repo_digest_tags[repo][group_key]['tags'].append(tag)

    # Now group by individual tags.
    # For every unique tag across the registry, find all images (repo + digest) that have it.

    builds_map = {} # tag_name -> { 'id': tag_name, 'tag': tag_name, 'images': [] }

    for repo, digests in repo_digest_tags.items():
        for group_key, data in digests.items():
            tag_list = sorted(data['tags'])

            # Format size and date
            size_mb = round(data['size'] / (1024 * 1024), 2)
            date_str = data['created']
            if 'T' in date_str:
                date_str = date_str.split('T')[0] + " " + date_str.split('T')[1][:8]

            # The UI shows "digest", let's use the display_digest (which is now mostly config_digest)
            # so it matches the Image ID that users see locally (e.g. 5caa9edcc796)
            display_digest = data.get('display_digest', group_key)
            if display_digest and display_digest.startswith('sha256:'):
                display_digest = display_digest.split('sha256:')[1][:12]

            image_info = {
                'repo': repo,
                'size_mb': size_mb,
                'created': date_str,
                'digest': display_digest,
                'all_tags': tag_list
            }

            for tag in tag_list:
                if tag not in builds_map:
                    builds_map[tag] = {
                        'id': tag,
                        'tag': tag,
                        'images': []
                    }
                builds_map[tag]['images'].append(image_info)

    # Convert builds map to a list sorted by date (latest first based on images inside)
    builds = list(builds_map.values())

    def get_latest_date(build):
        dates = [img['created'] for img in build['images'] if img['created'] != "Unknown"]
        return max(dates) if dates else ""

    for build in builds:
        build['latest_date'] = get_latest_date(build)

    builds.sort(key=lambda b: b['latest_date'], reverse=True)

    return builds

@app.route('/')
def index():
    builds = get_registry_data()
    return render_template('index.html', builds=builds)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
