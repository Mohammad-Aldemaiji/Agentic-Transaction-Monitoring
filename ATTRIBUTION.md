# Attribution and provenance

The project team remains responsible for reviewing, testing and submitting this work under its university's policies.

- The SPAP proposal is the requirements baseline. The user's historical `Senior Project V1.1 copy.zip` was reviewed for transaction fields, rolling features, scoring contracts, dashboard statistics and account-history behavior. The fresh backend does not import that archive's credentials, environments, bundled dependencies or agent instructions.
- Backend code, tests and documentation in `backend/`, `supabase/` and `docs/` were developed with OpenAI Codex assistance during October 2026. The implementation and verification evidence should be reviewed by the team before academic submission or deployment.
- The original repository's `src/agent/` prototype and model files predate this backend work and retain their own provenance. They are not deployed by the new backend containers.
- Runtime libraries include FastAPI, Starlette, Uvicorn, Pydantic, SQLAlchemy, psycopg, PyJWT and HTTPX. Exact versions and transitive dependencies are recorded in `backend/uv.lock`; their respective upstream licenses apply.
- Supabase provides managed PostgreSQL and identity. Docker provides application packaging; uv provides dependency/build tooling. Offline introductory speech uses eSpeak NG, installed from the base distribution's packages. Preserve its license notices and the licenses of container/distribution components when distributing images.
- pytest, Hypothesis, Ruff, mypy and pip-audit support development and verification. The backend's test data is synthetic; no external financial dataset was added by this backend implementation.
- Local HTTPS uses Caddy (Apache-2.0); local aggregate monitoring uses Prometheus (Apache-2.0) and its Python client. The local system-health page was developed for this project with Codex assistance. Caddy 2.11.7 and Prometheus 3.15.0 are rebuilt from their pinned upstream sources with Go 1.27.3 and golang.org/x/net 0.60.0 to address findings in the published binaries. Preserve upstream licenses/notices when distributing these images; these are local patched builds, not new upstream releases.
- Bandit and Trivy provide automated source/container vulnerability checks. They support the team's own review and do not constitute an independent penetration test.

This file records assistance and component provenance. It does not claim that third-party libraries, historical models or datasets were created by this team, or that licensing/security review is complete merely because a component is listed.

## Local integration, 26 October 2026

- E05 preprocessing, feature grouping and SHAP explanation code in `backend/src/atm_backend/e05_model.py` was adapted from Mohammad Aldemaiji's repository at commit `7922499702e896d4043f9b18af29455c121722ae`. Adaptations replace pickle metadata with equivalent JSON, accept an explicit artifact directory, format the code and remove a duplicate display-label entry. The model artifacts themselves are unchanged.
- The local investigator's evidence balance, recommendation vocabulary and local-only model restriction, plus the Piper/Whisper approach and PyAV audio decoding, derive from that same version. Codex adapted these into our scoped advisory workflow and bounded speech processes. Upstream's direct action execution and phone disposition rules were not copied into our approval path.
- Optional native dependencies include XGBoost, pandas, NumPy, Piper, faster-whisper, PyAV and their transitive libraries; versions are recorded in `backend/uv.lock`. Ollama/Qwen3 and downloaded voice/model weights retain their respective upstream terms. This integration does not claim authorship of those models.

## Face sign-in, 6 November 2026

- Face detection, landmarks and 128-number descriptors use face-api.js 0.22.2 by Vincent Mühler (MIT License, notice in `frontend/public/face/LICENSE`) and its published pretrained weights (`tiny_face_detector`, `face_landmark_68`, `face_recognition`), copied unchanged from the upstream repository into `frontend/public/face/`. The face-recognition model is a port of dlib's ResNet face descriptor; upstream terms apply.
- The capture flow, thresholds and scan animation were adapted from Mohammad Aldemaiji's FaceKey prototype. Server-side matching, enrollment, lockout and administrator reset, the live dashboard counters and the "AI assessment" label were developed with Claude (Anthropic) assistance and should be reviewed by the team before submission.
- End-to-end testing used computer-generated faces from NVIDIA's StyleGAN2 sample image as fake webcam input; no image of a real person was used or added to the repository.
