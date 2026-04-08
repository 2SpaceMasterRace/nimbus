+Inspired by John Carmack's plans.md files.

[TODO] Start proper documentation for the project \
  [DONE] Add proper git-workflow (assignment-amend) to CONTRIBUTION.md \
  [SUB-TODO] Read more into uv docs and look into extra flags \
  [DONE] Setup sphinx similar to attrs documentation 

[TODO ] Add Proper Tooling to the project  \
  [SUB-TODO] Look into property-based testing libraries like hypothesis \
  [SUB-TODO] Look into using graphite as a team, and make use of cool features like stacked diffs and pr's. \
  [DONE] Look into adding structured logging \
  [SUB-TODO] Setup CircleCI for the project and look into best practices and improving performance of the CI \
  [SUB-TODO] Look into publishing the project as an installable python package in PyPI pipeline \
  [SUB-TODO] Look into healthy python codebase hygiene and setup \
  [SUB-TODO] [Optional] Add AGENTS.md or SKILLS.md of some kind to help AI agents work better with the codebase 


[TODO] Development \
  [SUB-TODO] Look into mocks for testing \
  [SUB-TODO] Look into justfile and nix \
  [SUB-TODO] Setup codebase workflow just like Ghostty like using Issue Triages and such \
  [SUB-TODO] Look into open-source models from chatgpt, kimi, and mistral \
  [SUB-TODO] Look into Google Python Style Guide \
  [SUB-TODO] Look into storing credentials the right way like how other companies do it in production \
  [SUB-TODO] Learn how to write exceptionally clean interface design with notably low surface area and high functionality\
  [SUB-TODO] Learn how to write tests with comprehensive error modeling with typed domain exceptions \
  [SUB-TODO] Take a crash course on proper API design \
  [SUB-TODO] Look into resilience patterns (retries, rate-limit handling, idempotency)

Datadog, Prometheus, Grafana, OpenTelemetry, Jaeger
Docker, Kubernetes, Terraform, AWS, GitHub Actions, Buildkite

automated test runnner like tox


[NOTES] Code Review
  How To Review This Like A Senior Engineer

  Here is the senior review loop I want you to internalize:

  1. What is the contract?
  2. What are the resources?
  3. What can fail?
  4. What happens on cleanup?
  5. Do tests prove the important properties?

  Applied here:

  1. Contract

  - endpoint accepts bucket and object name
  - dependency provides storage client
  - response is a file download

  2. Resources

  - temp file on disk
  - storage client instance
  - HTTP response stream

  3. Failures

  - missing params
  - storage failure
  - object not found
  - temp file create/delete issue
  - hidden import/DI registration issues

  4. Cleanup

  - failure cleanup exists
  - success cleanup missing

  5. Test truth

  - tests prove happy path and some errors
  - tests do not prove lifecycle cleanup

  That is the difference between “I read the diff” and “I reviewed the code”.

 Most Important Improvement To Learn From This Commit

  The biggest lesson is not “remember to delete temp files.”

  It is this:

  Whenever code creates a resource,
  ask who owns its full lifecycle.

  Resource examples:

  - files
  - DB connections
  - network sockets
  - locks
  - temp directories
  - background jobs
  - transactions

  That one habit will make your reviews much sharper.

```text
Catch only the failures you understand and intend to translate.
Let unexpected bugs remain unexpected.
```


When reviewing or writing code, ask these 5 questions:

```text
1. What is the input?
2. What is the output?
3. What resources are created?
4. What can fail?
5. Who cleans up?
```

Applied here:

```text
Input:
- bucket_name
- object_name

Output:
- downloadable HTTP file response

Resources:
- temp file
- storage client
- HTTP response

Failures:
- invalid request
- missing object
- storage outage
- local file issue
- programmer bug

Cleanup:
- temp file must be deleted
```


