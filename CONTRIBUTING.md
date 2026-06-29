# Contributing to Collider

Thanks for taking the time to contribute. This page covers how to propose changes
responsibly. For the development environment, running tests, code style, and the
commit convention, see the
[development guide](https://collider.ee/latest/development/contributing/).

## Reporting issues

Open a [bug report or feature request](https://github.com/MaxandreOgeret/collider/issues/new/choose)
and include enough detail to reproduce the problem or understand the request.

## Pull requests

- Branch from and open pull requests against `main`.
- Use [Conventional Commits](https://www.conventionalcommits.org/)
  (`type(scope): description`); the commit linter and the full CI suite
  (pytest 3.10-3.13, ruff, ty, pylint) must pass.
- Keep each pull request focused, and explain the *why* in the description.
- Fill in the pull request template, including the checklist and the AI
  disclosure.

## Signing off your commits (DCO)

Collider uses the [Developer Certificate of Origin](https://developercertificate.org/):
every commit must carry a `Signed-off-by` line certifying that you wrote the change,
or otherwise have the right to submit it under the project's license. Add it with:

```bash
git commit -s -m "feat(scope): ..."
```

This appends `Signed-off-by: Your Name <your@email>` from your git `user.name` and
`user.email`. CI rejects pull requests whose commits are not signed off. To sign off
commits you have already made, run `git rebase --signoff <base-branch>` and
force-push the branch.

## Use of AI tools

AI assistance is allowed, but accountability is not optional. This project has
received unsolicited, low-effort, machine-generated pull requests; to keep review
time focused on genuine contributions:

- **Disclose** in the pull request whether AI tools were used for any part of the
  change.
- If AI was used, a **human must fully review every line**, understand it, and take
  **full responsibility** for its correctness, security, and quality -- the same as
  if it had been written by hand.
- Pull requests that appear to be unreviewed machine output, or that do not disclose
  AI use, may be closed without further review.

By opening a pull request you confirm the submission is your own work (or is properly
attributed) and that you stand behind it.
