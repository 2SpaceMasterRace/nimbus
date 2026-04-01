# Code Review Teaching Notes

## Start Here

Let us reset and build this from zero.

The simplest possible explanation of your project is:

```text
A user asks your app for a file
-> your app asks AWS S3 for that file
-> your app temporarily stores it on disk
-> your app sends it back over the web
```

That is the whole movie.

Now we will teach every piece of that movie in plain English.

## 1. What Even Is Happening?

When you open a website or call an API, you are sending an HTTP request.

HTTP is just a standardized message format.

Example:

```text
Client: "Please give me /download?bucket_name=my-bucket&object_name=report.csv"
Server: "Okay, here is the file" or "No, I couldn't find it"
```

Definitions:

- Client: the thing asking. Usually a browser, mobile app, curl, Postman, or another server.
- Server: the thing listening for requests and sending back responses.
- Request: the ask.
- Response: the answer.
- Route/endpoint: a specific address in your app, like `/health` or `/download`.
- `GET`: an HTTP method that means "read/fetch something".

ELI5:

- `GET` = "Can I look at this?"
- `POST` = "Please create something"
- `PUT` = "Replace this"
- `PATCH` = "Change part of this"
- `DELETE` = "Remove this"

So `GET /download` means:
"Please fetch a file for me."

## 2. How Your App Works End To End

Here is the full path in your project:

```text
User
-> Uvicorn
-> FastAPI app
-> /download route
-> get_storage_client()
-> CloudStorageClient
-> S3Client
-> AWS S3
-> temp file on disk
-> FileResponse
-> user gets file
-> cleanup deletes temp file
```

Now in human terms:

1. A user hits your FastAPI server.
2. FastAPI sees the path is `/download`.
3. FastAPI reads the query parameters:
   - `bucket_name`
   - `object_name`
4. FastAPI also sees your route needs a `client`.
5. It calls `get_storage_client()` to get one.
6. That returns an object that knows how to talk to cloud storage.
7. In this project, that concrete object is an `S3Client`.
8. The route creates a temp file.
9. The S3 client downloads the S3 object into that temp file.
10. FastAPI returns that file to the user.
11. After the response is sent, cleanup deletes the temp file.

That is the whole feature.

## 3. What Is Uvicorn? What Is FastAPI? What Is ASGI?

Think of these as different jobs.

- FastAPI: your app code. It decides what `/download` means.
- Uvicorn: the engine that runs your app and listens on a port.
- ASGI: the shared "language" that Uvicorn and FastAPI use to talk to each other.

Analogy:

- FastAPI is the restaurant kitchen.
- Uvicorn is the waiter taking orders and bringing food.
- ASGI is the agreed-upon format for the order tickets.

Without ASGI, Uvicorn and FastAPI would not know how to connect.

Diagram:

```text
Internet
-> socket connection
-> Uvicorn receives bytes
-> Uvicorn turns them into an ASGI request
-> FastAPI handles the request
-> FastAPI returns an ASGI response
-> Uvicorn sends bytes back to the client
```

Official references:

- [Uvicorn docs](https://www.uvicorn.org/)
- [ASGI docs](https://asgi.readthedocs.io/en/latest/)
- [FastAPI docs](https://fastapi.tiangolo.com/)

## 4. What Is a Package? How Do Imports Work?

A Python package is a folder of Python code that can be imported.

In this repo, examples are:

- `aws_client_service`
- `aws_client_impl`
- `cloud_storage_client_api`

Import means:
"Python, go find this code, load it, run the top-level code, and let me use it."

Example:

```python
import aws_client_impl
```

Python roughly does this:

```text
1. Look in memory: is it already imported?
2. If not, search places on sys.path
3. Find the package
4. Execute its top-level code once
5. Store the loaded module in sys.modules
6. Reuse that loaded module later
```

That "execute top-level code once" part matters a lot.

Because in your project:

```python
import aws_client_impl
```

does not just "make names available".

It also causes registration side effects.

That means importing the package runs code that says:

```text
"Hey global factory, if anyone asks for a CloudStorageClient,
give them an S3Client."
```

So this import is important for wiring.

Official reference:

- [Python import system](https://docs.python.org/3/reference/import.html)

## 5. What Is `uv`? Why Can You Import Packages In This Repo?

`uv` is the project/dependency manager you are using.

It handles things like:

- creating environments
- installing dependencies
- running tools like `pytest`, `ruff`, `mypy`
- workspace support

This repo uses a workspace. That means one repo contains multiple related packages managed together.

So when you run:

```bash
uv run pytest
```

`uv` runs pytest inside the project's managed environment with the workspace packages available.

That is why imports like:

```python
from aws_client_service.main import app
```

work cleanly.

Official references:

- [uv docs](https://docs.astral.sh/uv/)
- [uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/)

## 6. What Are Interfaces and Contracts?

This is one of the most important ideas in software.

A contract means:
"If you give me something that behaves like this, I know how to use it."

In your codebase, `CloudStorageClient` is the contract.

It says, roughly:

```text
A cloud storage client must know how to:
- upload a file
- upload a file-like object
- download a file
- list files
- delete a file
```

That contract is written as an abstract base class.

Why do this?

Because your FastAPI route should not care whether storage is:

- AWS S3
- Google Cloud Storage
- Azure Blob Storage
- fake in-memory test storage

It just wants "something that can download a file".

Analogy:

- A wall outlet is a contract.
- Your phone charger does not care which power plant generated the electricity.
- It only cares that the outlet provides the expected interface.

In your repo:

```text
CloudStorageClient = contract
S3Client = concrete implementation
```

Official reference:

- [Python `abc` docs](https://docs.python.org/3/library/abc.html)

## 7. What Is Dependency Injection?

Dependency injection means:
"Instead of building everything yourself inside a function, declare what you need, and have someone provide it."

Without DI:

```python
def download():
    client = S3Client(...)
```

That is tightly coupled.
The route now hardcodes AWS.

With DI:

```python
def download(client: CloudStorageClient):
    ...
```

Now the route says:
"I need a storage client, but I do not care exactly which one."

FastAPI supports this using `Depends`.

In your code:

```python
client: Annotated[CloudStorageClient, Depends(get_storage_client)]
```

This means:

- the variable is conceptually a `CloudStorageClient`
- FastAPI should call `get_storage_client()` to provide it

ELI5:

- The route is a chef.
- The chef says, "I need eggs."
- DI is the kitchen system that brings eggs.
- The chef does not go raise chickens.

Why this is great:

- easier testing
- looser coupling
- simpler code
- easier swapping of implementations

Official reference:

- [FastAPI dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/)

## 8. How Tests Override The Dependency

In the real app:

```text
get_storage_client() -> real S3Client
```

In tests:

```text
get_storage_client() -> fake mock client
```

That override happens here conceptually:

```python
app.dependency_overrides[get_storage_client] = lambda: mock_client
```

That means:
"FastAPI, whenever a route asks for `get_storage_client`, do not call the real one. Return this fake one instead."

That is why tests can simulate downloads without talking to AWS.

Diagram:

```text
Production:
route -> get_storage_client -> real S3Client -> AWS

Test:
route -> get_storage_client -> fake mock client -> no AWS
```

This is one of the cleanest parts of FastAPI.

## 9. What Is AWS S3 Really?

S3 is object storage.

Not a normal hard drive.
Not really folders in the traditional sense.

It stores:

- bucket
- key
- bytes

Example:

- bucket: `my-bucket`
- key: `reports/2026/q1.csv`
- bytes: the actual contents of the file

You can think of it like:

```text
bucket = warehouse
key = label on a box
object = actual box contents
```

Important:

- the "folders" in S3 are mostly just part of the key name
- `reports/2026/q1.csv` is really a string key, not a true nested directory tree

So when your API says:

```text
bucket_name=my-bucket
object_name=reports/archive.zip
```

it is saying:
"Go to bucket `my-bucket`, find the object whose key is `reports/archive.zip`, and download it."

## 10. Why Temp Files Exist Here

Your route needs to return a file response.
The storage client's API currently downloads into a local file path.
So the route needs a scratch place to hold the data briefly.

That is why it creates a temp file.

A temp file is just:
"a short-lived file created for temporary work"

Python has a built-in module for this:

- [Python `tempfile` docs](https://docs.python.org/3/library/tempfile.html)

Why you are allowed to create one:

- because your program is running on a machine with a filesystem
- the standard library gives you safe helpers for temp files
- the OS provides temp directories

Before the fix, the problem was:

- the temp file got created
- the file got sent
- nobody deleted it afterwards

After the fix:

- the response has a background cleanup task
- once the response finishes, the temp file is removed

Official reference:

- [Starlette background task docs](https://www.starlette.io/background/)
- [FastAPI custom response docs](https://fastapi.tiangolo.com/advanced/custom-response/)

## 11. What Is `FileResponse`?

`FileResponse` means:
"FastAPI/Starlette, please send this file to the client as the HTTP response."

It handles details like:

- reading the file
- setting headers
- setting content length
- streaming the content efficiently

It does not automatically mean:
"and delete that file afterwards"

That is why we had to attach cleanup explicitly.

## 12. What Is `content-disposition`? Why Does It Matter?

Headers are metadata in an HTTP response.

The body is the actual content.
Headers are extra instructions.

For file downloads, `content-disposition` is important because it tells the browser:

- this is a downloadable file
- here is the suggested filename

Example idea:

```text
Content-Disposition: attachment; filename="archive.zip"
```

Without correct headers:

- the browser may display instead of download
- the filename may be wrong
- the user experience may be confusing

So yes, header tests matter.

## 13. What Are The `_` Underscores?

Several uses:

`_bucket`, `_key`

- means "this parameter exists, but I do not use it"
- common in tests and throwaway variables

`self._bucket_name`

- leading underscore means "internal/private by convention"
- not enforced, but signals "do not treat this as public API"

`__name__`

- double underscore on both sides is a Python special name
- different thing entirely

So underscore is often a communication signal.

## 14. What Is `async` / `await`?

Plain version:

- some operations take time, especially network and disk
- `async` lets Python structure code so it can pause while waiting
- `await` means "pause here until this async work is ready"

Example:

```python
async def hello():
    await something()
```

ELI5:

- normal code is like waiting in line and refusing to do anything else
- async code is like taking a number and sitting down until called

Important distinction:

Concurrency:

- multiple tasks make progress during overlapping time

Parallelism:

- multiple tasks literally run at the same instant on different workers/cores

Analogy:

- concurrency = one chef juggling several dishes
- parallelism = three chefs each cooking one dish

Python docs say `asyncio` is for writing concurrent code using `async`/`await`.

Official reference:

- [Python asyncio docs](https://docs.python.org/3/library/asyncio.html)

In FastAPI:

- `async def` routes are good when you do async I/O
- plain `def` routes are fine too
- FastAPI can work with both

## 15. What Is Typing? What Is `Annotated`? What Is `Any`?

Type hints are notes you attach to code to describe expected shapes.

Example:

```python
def greet(name: str) -> str:
    return "Hi " + name
```

This means:

- `name` should be a string
- result should be a string

Python itself usually does not enforce these at runtime.
Tools like `mypy`, editors, and linters use them.

Official reference:

- [Python typing docs](https://docs.python.org/3/library/typing.html)

Important ones for you:

`str`

- string

`dict[str, str]`

- dictionary from strings to strings

`CloudStorageClient`

- value should behave like that interface

`Any`

- "type checker, stop checking this precisely"
- useful sometimes, but use carefully

`Annotated[T, extra]`

- "the real type is `T`, and here is extra metadata"

In FastAPI:

```python
Annotated[CloudStorageClient, Depends(get_storage_client)]
```

means:

- type is `CloudStorageClient`
- FastAPI metadata says how to get it

Why modern Python likes `Annotated`:

- keeps type info and framework info together
- works well with tools like FastAPI and mypy

## 16. Why Broad `except Exception` Is Dangerous

This matters a lot.

If you write:

```python
except Exception:
    raise HTTPException(status_code=502, detail="storage error")
```

you are saying:
"Every possible problem here is a storage problem."

But that is false.

Possible failures include:

- AWS is down
- object missing
- local disk full
- temp directory permissions bad
- your own code has a bug
- wrong variable name
- cleanup logic broken

Those are not all the same.

A good rule:

```text
Catch only the failures you understand and intend to translate.
Let unexpected bugs remain unexpected.
```

Why?
Because status codes should tell the truth.

Examples:

- user forgot a required query parameter -> `422`
- object not found -> `404`
- upstream AWS/storage issue -> `502`
- your app has a bug -> `500`

If you turn everything into `502`, debugging gets worse and your API lies.

## 17. Why The Boolean Contract Is Weak

This part is subtle but important.

If `download_file()` returns only:

- `True`
- `False`

then `False` can mean many things:

- object missing
- permission denied
- timeout
- bucket missing
- local write failed
- unknown problem

That is not enough information.

Better contracts include:

1. Specific exceptions

```python
raise ObjectNotFoundError(...)
raise StorageUnavailableError(...)
```

2. Rich result objects

```python
DownloadResult(status="not_found")
DownloadResult(status="ok", path="...")
```

3. Returning actual domain data

The reason senior engineers care is:
good contracts preserve meaning.

Bad contracts blur meaning.

## 18. How To Think About Fixing Code Like This

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

That is how you think like a senior reviewer.

## 19. Unit, Integration, and E2E Tests

Unit test:

- tests one small thing in isolation
- fake the rest

In this project:

- test the route with a fake storage client
- no AWS involved

Integration test:

- tests whether pieces are wired together correctly

In this project:

- does FastAPI dependency injection really return an `S3Client`?
- does app wiring behave correctly?

E2E test:

- tests the whole system as a user sees it

Real e2e would mean:

- running server
- sending real HTTP
- possibly talking to real AWS

Pytest basics:

- test files named `test_*.py`
- test functions named `test_*`
- fixtures are reusable setup helpers

Official references:

- [pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html)
- [pytest good practices](https://docs.pytest.org/en/stable/explanation/goodpractices.html)

## 20. How To Write These Tests Normally

Pattern for a unit test:

```text
Arrange:
- create fake dependency
- set overrides

Act:
- call route

Assert:
- check status code
- check body
- check headers
- check side effects
```

That is exactly what your download endpoint tests do.

For example:

- fake storage client writes bytes into destination file
- route returns those bytes
- test asserts response body
- test asserts temp file was cleaned up

That is solid testing.

## 21. What We Changed In The Code

We changed the route so the temp file is deleted after the response is sent.

Conceptually:

```text
before:
create temp file -> send file -> temp file remains

after:
create temp file -> send file -> background cleanup deletes file
```

We also tested:

- cleanup happens
- filename header uses just `archive.zip`, not full S3 key path

## 22. Real World Mental Model

Think of this endpoint like a hotel front desk.

```text
Guest asks for package
-> front desk calls storage room
-> staff pulls package from storage
-> package is placed briefly on counter
-> guest receives it
-> counter is cleared
```

Bad version:

- counter keeps filling with forgotten packages

Good version:

- counter is cleared after each handoff

That is the temp-file bug in plain English.

## 23. Best Beginner Summary

If you remember only this, remember this:

```text
FastAPI route = function that handles a web request
Depends(...) = ask FastAPI to provide something you need
CloudStorageClient = promise/contract for storage behavior
S3Client = AWS-specific implementation of that promise
Uvicorn = program that runs the web app
ASGI = the protocol between server and app
temp file = short-lived local scratch file
FileResponse = send a file as HTTP response
pytest = test runner
unit test = isolated behavior
integration test = wiring between parts
e2e test = full system path
```

## Sources

- [Python import system](https://docs.python.org/3/reference/import.html)
- [Python asyncio](https://docs.python.org/3/library/asyncio.html)
- [Python typing](https://docs.python.org/3/library/typing.html)
- [Python abc](https://docs.python.org/3/library/abc.html)
- [Python tempfile](https://docs.python.org/3/library/tempfile.html)
- [FastAPI dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/)
- [FastAPI custom responses](https://fastapi.tiangolo.com/advanced/custom-response/)
- [Starlette background tasks](https://www.starlette.io/background/)
- [pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html)
- [pytest good practices](https://docs.pytest.org/en/stable/explanation/goodpractices.html)
- [uv docs](https://docs.astral.sh/uv/)
- [uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/)
- [Uvicorn docs](https://www.uvicorn.org/)
- [ASGI docs](https://asgi.readthedocs.io/en/latest/)
