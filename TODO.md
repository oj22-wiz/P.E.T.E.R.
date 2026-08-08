# P.E.T.E.R. — Mobile Cloud Deploy (all features, no screen share)

## Plan
Make the whole app (frontend + backend + Peter worker) run from ONE public link
so it works from the iPhone Home Screen even when the PC is off. All features
except screen sharing.

## Steps
- [x] Make `mobile_backend.py` cloud-ready (public URL, Spotify callback, files endpoints, cross-platform worker launch).
- [x] Add public Spotify OAuth helpers to `spotify_auth.py` (get_authorize_url / handle_callback).
- [ ] Add missing mobile features to `mobile/index.html` (Files upload + chat transcript).
- [ ] Add deployment config: `Dockerfile`, `render.yaml`, update READMEs with cloud env vars + deploy/install steps.
- [ ] Verify edits.

