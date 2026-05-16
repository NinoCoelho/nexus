"""Nexus account auth — Firebase-issued idToken exchange + tier polling.

The desktop app is single-user. ``nexus_account`` handles the one Nexus
account the user might be signed into; ``status_watcher`` polls the
website's /api/status endpoint to keep tier + budget visible to the UI.
"""
