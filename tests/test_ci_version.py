"""Fork builds use upstream releases and their own workflow run counter."""
import argparse

from scripts import ci_version


def test_fork_uses_separate_release_repository(monkeypatch):
    requested = []

    def fetch(url, collection, token):
        requested.append(url)
        if url.endswith('/repos/upstream/app/releases'):
            return [{'tag_name': 'V1.2.3', 'published_at': '2026-01-01T00:00:00Z'}]
        if url.endswith('/repos/fork/app/actions/workflows/ci.yaml/runs'):
            return [{'id': 9, 'run_number': 1, 'event': 'pull_request',
                     'head_branch': 'feature', 'created_at': '2026-01-02T00:00:00Z'}]
        return []

    monkeypatch.setattr(ci_version, 'fetch_paginated_collection', fetch)
    args = argparse.Namespace(releases_json=None, runs_json=None, repo='fork/app',
        releases_repo='upstream/app', github_api_url='https://api.github.com',
        workflow_file='ci.yaml', github_token=None)
    releases, runs = ci_version.load_releases_and_runs(args)
    assert ci_version.build_ci_version(releases, runs, 'alpha', 9, 1) == 'V1.2.4-alpha.1'
    assert requested == ['https://api.github.com/repos/upstream/app/releases',
                         'https://api.github.com/repos/fork/app/actions/workflows/ci.yaml/runs']
