# Dependency Mapping

This script generates a mermaid diagram of the dependencies between issues in a GitHub repository based on the ["Blocked by" and "Blocking"](https://github.blog/changelog/2025-08-21-dependencies-on-issues/#%e2%9e%95-getting-started) relationships feature.

## Usage

### Mapping dependencies for all deliverables

This will generate a diagram of the dependencies between all deliverables in the repository (optionally with a specific issue type), then update the [`documentation/dependencies.md`](../../../documentation/dependencies.md) file with the diagram.

```bash
python run.py \
    --org simpler-coop \
    --repo simpler-template \
    --project 1 \
    --scope repo
```

### Mapping dependencies for a single deliverable

This will generate a diagram of the dependencies upstream and downstream of a specific deliverable, and post the diagram to the "Dependencies" section within each issue's body.

```bash
python run.py \
    --org simpler-coop \
    --repo simpler-template \
    --project 1 \
    --scope issue
```

## Configuring the sync behavior

The CLI supports the following options:

- `--org`: The GitHub organization that owns the repository
- `--repo`: The GitHub repository to fetch issues from
- `--project`: The GitHub project to use for issue status
- `--issue-type`: Optional GitHub issue type to include in the dependency mapping. When omitted, issues of all types are included.
- `--statuses`: Optional GitHub issue statuses to include in the dependency mapping, can be specified multiple times. If omitted, issues with all statuses are included.
- `--batch`: The number of issues to fetch at a time, defaults to `100` which is the max batch size for the GitHub API
- `--scope`: The scope of the dependencies to map (`issue` or `repo`)
- `--dry-run`: Whether to run the mapping in dry run mode (e.g. log the GitHub updates but don't actually perform them)

For repository mappings, a matching project status takes precedence. If an issue has no status in the selected project, closed GitHub issues are shown as `Done` and open issues default to `Todo`. If no repository issues are returned as items in the selected project, the mapping exits without writing a diagram; verify the project number and that the API token can read the project.

## Local development

Before running the script locally, you need to set the `GITHUB_API_TOKEN` environment variable.

```bash
python run.py \
    --org "simpler-coop" \
    --repo "simpler-template" \
    --project 1 \
    --scope issue
```

You can also run the script with the `--dry-run` flag to see what would be done without actually performing the actions.

## Running the GitHub Actions locally

The workflows can be run locally with [`act`](https://nektosact.com/), which uses Docker to execute the workflow steps. Docker, `act`, and the repository's ignored `.secrets` file are configured for local runs. Add these entries to `.secrets` without committing the file:

```text
GITHUB_TOKEN=<personal-access-token>
GH_TOKEN_PROJECT_ACCESS=<personal-access-token>
```

Both values can use the same token. It needs access to read the repository issues and the organization project fields queried by the script (`read:project` for a classic PAT, or the corresponding Projects read permission for a fine-grained PAT). The issue-level workflow is forced into `--dry-run` mode during local runs; it reads GitHub data but does not update issues. The repo-level workflow writes the diagram to `documentation/dependencies.md` in your local checkout and skips its commit and push steps.

In VS Code, run **Tasks: Run Task** and choose one of:

- `Actions: deps mapping issue (local dry run)`
- `Actions: deps mapping repo (local output)`

Alternatively, run from the repository root:

```bash
act workflow_dispatch --workflows .github/workflows/deps-mapping-issue.yml --env LOCAL_ACTIONS=true
act workflow_dispatch --workflows .github/workflows/deps-mapping-repo.yml --env LOCAL_ACTIONS=true
```

The checked-in `.actrc` loads `.secrets` automatically, selects the `linux/amd64` runner image on Apple Silicon, and bind-mounts the workspace into the container. The VS Code tasks set the repository owner and name explicitly because `act` cannot parse SSH remotes that use a custom host alias. The issue workflow only prints the proposed issue-body updates locally. The repo workflow changes the local Markdown file; inspect it with `git diff -- documentation/dependencies.md`. Neither local task pushes changes to GitHub or starts a GitHub Actions run.
