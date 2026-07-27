## 1. Data Model and Migration

- [x] 1.1 Add the Cookie metadata model, SQLite table, unique-name constraint, indexes, and CRUD/status repository methods.
- [x] 1.2 Add the nullable `share_links.cookie_id` foreign key and update share-link models, queries, and serialization.
- [x] 1.3 Implement idempotent migration of the legacy persisted Cookie to a default Cookie and associate existing share links without altering indexes, mappings, or history.
- [x] 1.4 Implement the environment-only `BDPAN_COOKIE` compatibility path without persisting or exposing its value.

## 2. Secret Storage and Validation

- [x] 2.1 Implement a Cookie secret repository backed by atomic `secrets.json` replacement, including masked summaries and orphan-state safeguards.
- [x] 2.2 Add Cookie input validation, normalized unique names, and log/error redaction for Cookie values and sensitive fields.
- [x] 2.3 Implement three-state Cookie validation that records Beijing-time metadata and distinguishes conclusive authentication failure from transient or risk-control errors.
- [x] 2.4 Persist client-updated Cookie fields back to the correct Cookie ID without affecting other stored Cookies.

## 3. Backend API and Task Integration

- [x] 3.1 Add authenticated Cookie list/detail endpoints and administrator-only create, update, delete, and validate endpoints.
- [x] 3.2 Reject deletion of referenced Cookies with an actionable conflict response and prevent missing or invalid Cookie IDs on share-link writes.
- [x] 3.3 Extend share-link create, update, list, and state APIs with safe Cookie association metadata.
- [x] 3.4 Replace global Cookie lookup with a share-link-aware client factory across indexing, refresh, browsing, download, manual sync, and scheduled sync paths.
- [x] 3.5 Ensure queued tasks use a start-time Cookie snapshot and fail safely when the association or secret cannot be resolved.
- [x] 3.6 Update CLI Cookie and share-link commands to work with the multi-Cookie model while retaining documented compatibility behavior.

## 4. Vue User Interface

- [x] 4.1 Replace the single-Cookie settings form with a responsive Cookie list showing name, masked summary, addition time, last validation time, and validity state.
- [x] 4.2 Add administrator interactions for creating, editing, validating, and deleting Cookies, including confirmation and referenced-Cookie conflict feedback.
- [x] 4.3 Add Cookie selection and current association display to share-link create, edit, and list views.
- [x] 4.4 Enforce page and administrator permissions in the UI while treating backend authorization as authoritative.
- [x] 4.5 Build the Vue production bundle and verify no Cookie secret appears in generated UI state or browser API responses.

## 5. Tests and Verification

- [x] 5.1 Add database and migration tests for fresh install, legacy secrets, repeated startup, associations, uniqueness, and referenced-Cookie deletion.
- [x] 5.2 Add secret repository and validation tests for atomic writes, masking, status transitions, transient failures, risk control, and redaction.
- [x] 5.3 Add FastAPI authorization and CRUD tests covering administrator, configuration-page user, share-page user, and unauthorized access.
- [x] 5.4 Add task/client integration tests proving each share link uses its associated Cookie for refresh, browse, download, manual sync, and scheduled sync.
- [x] 5.5 Run the complete backend unit and smoke test suite plus frontend production build, and resolve all failures.

## 6. Documentation and Delivery

- [x] 6.1 Update README with multi-Cookie configuration, status meanings, share-link association, migration, operation, and access instructions.
- [x] 6.2 Update the requirements specification and design document with the new data model, APIs, permissions, security, migration, and Beijing-time behavior.
- [x] 6.3 Update Jenkins pipeline checks as needed for migrations, tests, frontend build, and preservation of the entire `data` directory.
- [x] 6.4 Validate the OpenSpec change, review the final diff for secret leakage, and confirm all acceptance scenarios are covered.
- [x] 6.5 After implementation is committed and pushed, manually trigger the Jenkins build and provide the deployed service entry for user acceptance.
