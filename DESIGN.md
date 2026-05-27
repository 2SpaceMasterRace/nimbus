# Design Document

**Date:** 2026-02-28
**Status:** In Progress

---

## Context and Scope

This project is a clean Python wrapper around AWS S3. AWS S3 is a cloud object storage service that lets you store files and data at scale, but interacting with it directly requires dealing with boto3, AWS credentials, region configuration, and provider specific types. The goal is to hide all of that behind a simple, provider agnostic interface so that callers never need to know they are talking to S3.

The project is split into two components: `cloud_storage_client_api` which defines the abstract contract, and `aws_client_impl` which fulfills that contract using AWS S3 and boto3.

The long-term vision is to connect an LLM to this client so users can query their cloud storage in natural language — for example, asking how many files were uploaded in a given month and receiving a direct answer.

---

## Goals and Non-Goals

### Goals

- Define a minimal, provider-agnostic interface for cloud storage operations.
- Implement the interface concretely using AWS S3 and boto3.
- Keep the interface free of any AWS-specific details or dependencies.
- Handle large file uploads automatically using multipart upload.
- Load all credentials from environment variables, never hardcoded.
- Wire the implementation to the interface via Dependency Injection.

### Non-Goals

- Supporting multiple cloud providers simultaneously in this iteration.
- Building a server or API layer on top of the client (planned for future homework).
- Implementing the LLM query layer in this iteration.

---

## Learnings and Progress

The interface is defined as an Abstract Base Class in `cloud_storage_client_api`. It exposes:

- `upload_file(local_path, remote_path)` upload a file by path
- `upload_obj(file_obj, remote_path)` upload a binary file-like object
- `download_file(container, object_name, file_name)` download a file
- `list_files(prefix)` list files, optionally filtered by prefix
- `delete_file(container, object_name)` delete a file

A `get_client()` factory function is also exposed. By default it raises `NotImplementedError` and is replaced by the implementation via Dependency Injection when `aws_client_impl` is imported.

`S3Client` inherits from `CloudStorageClient` and implements all abstract methods. Key implementation decisions:

- Files larger than 100 MB are automatically routed to multipart upload.
- Multipart uploads are automatically aborted if any part fails, preventing lingering AWS charges.
- `structlog` is used for structured logging throughout.
- Authentication is handled entirely via environment variables.

---

## Cross-Cutting Concerns

- Credentials must never be hardcoded. All secrets are loaded from environment variables at runtime for local development. In CI, the pipeline authenticates via OIDC and assumes an IAM role directly — no long-lived access keys are stored anywhere. Missing variables will raise a `KeyError` immediately, which is intentional
- The interface has zero coupling to the implementation. Swapping to a different provider (GCP, Dropbox, etc.) only requires writing a new implementation package.
- Multipart upload introduces complexity but is necessary for large files. The abort-on-failure pattern prevents AWS from charging for incomplete uploads indefinitely.
- Future iterations will add a server layer on top of this client, so keeping the interface clean now reduces future refactoring cost.

---

## Review & Suggestions

**Purpose:**
- Share feedback.
- Discuss scope improvements.
- Suggest refinements.