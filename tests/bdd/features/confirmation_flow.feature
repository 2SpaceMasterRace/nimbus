Feature: Destructive confirmation flow
  Destructive storage actions require explicit, same-actor confirmation.

  Scenario: Delete intent returns a machine-readable confirmation prompt
    Given the wrapper signing secret is configured
    And the wrapper sends a Slack message "delete reports/2024/old.csv" with event id "evt-bdd-delete-request"
    When the wrapper posts the signed chat turn
    Then the response status is 200
    And the response outcome is "confirmation_required"
    And the response confirmation flag is true
    And the response confirmation kind is "delete_file"
    And the response expected confirmation reply is "yes, delete reports/2024/old.csv"
    And the storage client has not deleted any files

  Scenario: Same actor can confirm a pending delete
    Given the wrapper signing secret is configured
    And a pending delete exists for "reports/2024/old.csv"
    And the wrapper sends a Slack message "yes, delete reports/2024/old.csv" with event id "evt-bdd-delete-confirm"
    When the wrapper posts the signed chat turn
    Then the response status is 200
    And the response outcome is "reply"
    And the response text is "Deleted `reports/2024/old.csv`."
    And the storage client deleted "reports/2024/old.csv"

  Scenario: Different actor cannot confirm a pending delete
    Given the wrapper signing secret is configured
    And a pending delete exists for "reports/2024/old.csv"
    And user "U999OTHER" sends a Slack message "yes, delete reports/2024/old.csv" with event id "evt-bdd-delete-wrong-actor"
    When the wrapper posts the signed chat turn
    Then the response status is 200
    And the response outcome is "error"
    And the response text contains "original requester"
    And the storage client has not deleted any files
