Feature: Signed wrapper chat turns
  The Slack wrapper needs one stable Nimbus contract for normal chat turns.

  Scenario: Signed Slack message returns a reply outcome
    Given the wrapper signing secret is configured
    And the wrapper sends a Slack message "What files are under reports/?" with event id "evt-bdd-reply"
    When the wrapper posts the signed chat turn
    Then the response status is 200
    And the response outcome is "reply"
    And the response text is "Hello from Nimbus!"
    And the response confirmation flag is false
    And the response conversation id is "slack:T123TEAM:C123CHAN:1713840000.000001"
    And the AI client received the turn with the full storage tool surface

  Scenario: Signed Slack message can exercise the read-only file info tool
    Given the wrapper signing secret is configured
    And the AI client will call the "get_file_info" tool for "reports/april.csv"
    And the wrapper sends a Slack message "Show details for reports/april.csv" with event id "evt-bdd-info"
    When the wrapper posts the signed chat turn
    Then the response status is 200
    And the response outcome is "reply"
    And the response text contains "reports/april.csv"
    And the storage client recorded an info lookup for "reports/april.csv"
