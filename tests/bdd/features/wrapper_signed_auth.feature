Feature: Wrapper signed-request authentication
  Nimbus must fail closed when wrapper-to-service auth is missing, stale, replayed, or tampered.

  Scenario: Missing signed headers are rejected
    Given the wrapper signing secret is configured
    And the wrapper sends a Slack message "hello" with event id "evt-bdd-missing-auth"
    When the wrapper posts the chat turn without signed headers
    Then the response status is 401
    And the response detail contains "Missing signed-request headers"

  Scenario: Tampered body is rejected
    Given the wrapper signing secret is configured
    And the wrapper sends a Slack message "hello" with event id "evt-bdd-tamper"
    When the wrapper posts a tampered chat turn with the original signature
    Then the response status is 401
    And the response detail contains "Invalid signed request"

  Scenario: Replayed nonce is rejected
    Given the wrapper signing secret is configured
    And the wrapper sends a Slack message "hello" with event id "evt-bdd-replay"
    When the wrapper posts the same signed chat turn twice with nonce "nonce-bdd-replay"
    Then the first response status is 200
    And the second response status is 401
    And the second response detail contains "nonce has already been used"
