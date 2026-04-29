Feature: Attachment byte ingestion
  The wrapper can hand Nimbus bounded inline attachment bytes for upload-style turns.

  Scenario: Inline attachment bytes upload successfully
    Given the wrapper signing secret is configured
    And the wrapper attaches file "report.txt" containing "quarterly report"
    And the wrapper sends a Slack message "upload these files to finance/april" with event id "evt-bdd-upload"
    When the wrapper posts the signed chat turn
    Then the response status is 200
    And the response outcome is "reply"
    And the response model is "nimbus-runtime"
    And the storage client uploaded "finance/april/report.txt"

  Scenario: Mixed attachment upload returns partial success
    Given the wrapper signing secret is configured
    And uploading "finance/april/fail.txt" will fail
    And the wrapper attaches file "ok.txt" containing "ok"
    And the wrapper attaches file "fail.txt" containing "fail"
    And the wrapper sends a Slack message "upload these files to finance/april" with event id "evt-bdd-upload-partial"
    When the wrapper posts the signed chat turn
    Then the response status is 200
    And the response outcome is "partial_success"
    And the response text contains "skipped 1"
    And the storage client uploaded "finance/april/ok.txt"

  Scenario: Metadata-only upload request returns an error outcome
    Given the wrapper signing secret is configured
    And the wrapper attaches metadata-only file "report.txt"
    And the wrapper sends a Slack message "upload these files to finance/april" with event id "evt-bdd-upload-metadata-only"
    When the wrapper posts the signed chat turn
    Then the response status is 200
    And the response outcome is "error"
    And the response text contains "not provided"
    And the storage client has not uploaded any files
