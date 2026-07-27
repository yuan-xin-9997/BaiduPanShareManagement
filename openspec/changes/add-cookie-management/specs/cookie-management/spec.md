## ADDED Requirements

### Requirement: Manage multiple named Cookies
The system SHALL allow an administrator to create, update, and delete multiple named Baidu Netdisk Cookies, SHALL require each normalized name to be unique, and SHALL keep Cookie values outside browser-readable responses and application logs.

#### Scenario: Administrator adds a Cookie
- **WHEN** an administrator submits a unique name and a Cookie containing the required login fields
- **THEN** the system persists the secret, records its addition time, sets its validity to `unknown`, and returns metadata without the original Cookie value

#### Scenario: Administrator updates a Cookie
- **WHEN** an administrator changes a Cookie name or replaces its secret value
- **THEN** the system updates the metadata and secret atomically enough to prevent partial browser-visible state, and a replaced value resets validity to `unknown`

#### Scenario: Referenced Cookie cannot be deleted
- **WHEN** an administrator attempts to delete a Cookie associated with one or more share links
- **THEN** the system rejects the deletion and identifies that the share links must be reassigned first

#### Scenario: Unauthorized user attempts mutation
- **WHEN** a non-administrator attempts to add, update, delete, or validate a Cookie
- **THEN** the system rejects the operation without revealing Cookie secret data

### Requirement: Display safe Cookie metadata
The system SHALL show users with system-configuration access each Cookie's name, masked summary, addition time, most recent validation time, and current validity state, and SHALL present all times in Beijing time.

#### Scenario: User views configured Cookies
- **WHEN** an authorized user opens the system configuration page
- **THEN** the system lists all Cookies with `unknown`, `valid`, or `invalid` state and never returns the complete Cookie value

#### Scenario: Cookie has not been validated
- **WHEN** a newly added or changed Cookie has not completed a conclusive validation
- **THEN** the system displays `unknown` and an empty most recent validation time

### Requirement: Validate Cookie status
The system SHALL support explicit validation of an individual Cookie and SHALL update validity only when the Baidu response conclusively proves success or authentication failure.

#### Scenario: Validation succeeds
- **WHEN** an administrator validates a Cookie and an authenticated Baidu request succeeds
- **THEN** the system records the validation time and marks the Cookie `valid`

#### Scenario: Authentication is rejected
- **WHEN** validation receives a response that conclusively indicates the Cookie is expired or unauthenticated
- **THEN** the system records the validation time, marks the Cookie `invalid`, and returns a sanitized explanation

#### Scenario: Validation is inconclusive
- **WHEN** validation encounters a timeout, network failure, risk-control response, or other result that does not prove authentication failure
- **THEN** the system records the attempted validation without changing a previously known validity state and returns a sanitized explanation

### Requirement: Associate share links with Cookies
The system SHALL allow each share link to reference one configured Cookie and SHALL use that Cookie for every index, refresh, browse, download, and synchronization operation derived from the share link.

#### Scenario: User creates an associated share link
- **WHEN** a user with share-link access creates a share link and selects an existing Cookie
- **THEN** the system stores the association and submits indexing with the selected Cookie

#### Scenario: User reassigns a share link
- **WHEN** an authorized user edits a share link and selects a different existing Cookie
- **THEN** subsequent operations for that share link use the newly selected Cookie while already-running tasks retain their start-time credential snapshot

#### Scenario: Association is missing at execution
- **WHEN** a task starts for a share link whose Cookie association or secret cannot be resolved
- **THEN** the task fails safely with an actionable message that does not expose any Cookie value

### Requirement: Preserve existing installations
The system MUST migrate an existing persisted single Cookie and existing share links to the multi-Cookie model idempotently without losing secrets, share links, indexes, mappings, or task history.

#### Scenario: Persisted legacy Cookie is migrated
- **WHEN** the upgraded application starts with a Cookie in the legacy secrets-file field
- **THEN** the system creates one default Cookie record, moves the secret into the multi-Cookie store, and associates all previously unassociated share links with it

#### Scenario: Migration runs again
- **WHEN** the application restarts after the migration has already completed
- **THEN** it does not create duplicate Cookie records or alter existing share-link associations

#### Scenario: Environment-only Cookie remains configured
- **WHEN** the application starts with only `BDPAN_COOKIE` and no persisted Cookie records
- **THEN** existing operations retain a read-only compatibility path without copying the environment secret into persisted storage or exposing it to the browser
