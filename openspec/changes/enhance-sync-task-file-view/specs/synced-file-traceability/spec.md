## ADDED Requirements

### Requirement: Persist successfully synchronized files
The system SHALL persist a traceable file event for every file that is successfully added or updated in local storage by a synchronization run. Each event SHALL identify the synchronization mapping, synchronization run, relative path, file name, synchronization action, and synchronization time. The system SHALL NOT record skipped, deleted, or failed files as successfully synchronized files.

#### Scenario: New file is synchronized
- **WHEN** a synchronization run successfully downloads a file that did not previously exist in the mapping target
- **THEN** the system records an `added` file event associated with that mapping and run

#### Scenario: Existing file is updated
- **WHEN** a synchronization run successfully replaces an existing file with updated content
- **THEN** the system records an `updated` file event associated with that mapping and run

#### Scenario: File synchronization fails
- **WHEN** downloading or replacing a file fails
- **THEN** the system does not record that file as successfully synchronized

### Requirement: View files synchronized by a mapping
The system SHALL provide a “查看” action for every synchronization mapping. Activating the action SHALL open a modal listing all files known to have been synchronized to that mapping's local target, with repeated synchronization events for the same relative path represented by the latest event.

#### Scenario: Mapping has synchronized files
- **WHEN** an authorized user activates “查看” for a mapping with synchronized file events
- **THEN** the system opens a modal containing the latest record for every distinct synchronized relative file path

#### Scenario: Mapping has no synchronized file records
- **WHEN** an authorized user activates “查看” for a mapping with no recorded synchronized files
- **THEN** the system opens the modal and displays an explicit empty state

### Requirement: View files synchronized by a task or run
The task center SHALL provide a file-view action for a current synchronization task or synchronization-history run when that item has one or more recorded synchronized files. The action SHALL open the same file-list modal used by synchronization mappings and SHALL show only files associated with that synchronization run.

#### Scenario: Current synchronization task has recorded files
- **WHEN** a current synchronization task is associated with a run that has one or more synchronized file events
- **THEN** the task displays a view action that opens the run's synchronized-file list

#### Scenario: Synchronization history has recorded files
- **WHEN** a synchronization-history run has one or more synchronized file events
- **THEN** the history item displays a view action that opens that run's synchronized-file list

#### Scenario: Run has no recorded files
- **WHEN** a current task or history run has no synchronized file events
- **THEN** the system does not present an enabled file-view action for that item

### Requirement: Sort synchronized file lists
The synchronized-file modal SHALL support sorting by file name and synchronization time in both ascending and descending order. Sorting SHALL be deterministic when primary sort values are equal.

#### Scenario: Sort by file name
- **WHEN** the user selects file-name sorting and chooses a direction
- **THEN** the system lists files by file name in the selected direction and uses relative path as a stable tie-breaker

#### Scenario: Sort by synchronization time
- **WHEN** the user selects synchronization-time sorting and chooses a direction
- **THEN** the system lists files by synchronization time in the selected direction and uses relative path as a stable tie-breaker

### Requirement: Current tasks contain only active work
The task center's current-task collection SHALL include only tasks whose status is `queued` or `running`. Tasks in `success` or `failed` status SHALL be excluded from the current-task collection.

#### Scenario: Synchronization task completes successfully
- **WHEN** a synchronization task transitions to `success`
- **THEN** it is removed from current tasks and its persistent synchronization run remains available in synchronization history

#### Scenario: Synchronization task fails
- **WHEN** a synchronization task transitions to `failed`
- **THEN** it is removed from current tasks and its persistent synchronization run remains available in synchronization history

#### Scenario: Task is queued or running
- **WHEN** a task has status `queued` or `running`
- **THEN** it appears in the current-task collection

### Requirement: Protect synchronized file metadata
The system SHALL enforce server-side page authorization for synchronized-file queries and SHALL expose only file metadata needed by the interface, without exposing an arbitrary filesystem browsing capability.

#### Scenario: User views mapping files
- **WHEN** a user with synchronization-mapping page permission requests files for a valid mapping
- **THEN** the system returns the permitted synchronized-file metadata

#### Scenario: User views task history files
- **WHEN** a user with task-center page permission requests files for a valid synchronization run
- **THEN** the system returns the permitted synchronized-file metadata

#### Scenario: User lacks required permission
- **WHEN** a user without the required page permission requests synchronized-file metadata
- **THEN** the system rejects the request without returning file metadata

#### Scenario: Unsupported sort input
- **WHEN** a synchronized-file query supplies an unsupported sort field or direction
- **THEN** the system rejects the request or applies a documented safe default without interpolating the unsupported value into SQL
