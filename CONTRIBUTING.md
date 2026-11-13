# Contributing

First of all, thank you for considering contributing! We welcome contributions from everyone, including bug reports, feature requests, documentation improvements, and code enhancements.

This document outlines the process and guidelines to ensure smooth collaboration.

## How to Contribute

### 1. Fork the Repository
Create a personal fork of the repository and clone it to your local machine.

```bash
git clone https://github.com/your-username/your-repo.git
```

### 2. Create a Branch

Always create a new branch for your work. Use descriptive branch names:

```bash
git checkout -b feature/my-new-feature
git checkout -b bugfix/fix-issue-123
```

### 3. Follow the Code Style

We use **Pylint** with the settings in `.pylintrc` (the same check runs on GitHub for every push).
User-facing text (windows, menus, log messages) is in English. The program must keep working on
Windows, macOS and Linux.

```bash
pip install -r requirements.txt pylint pytest
python -m pylint --disable=broad-exception-caught $(git ls-files '*.py' '*.pyw' ':!:tests/*')
```

### 4. Write Clear Commits

- Use concise commit messages (less than 50 characters for the title).
- Include a more detailed description if needed.

Example:
```
Add new utility function for matrix inversion

This function handles small matrices with optimized performance and includes comprehensive tests.
```

### 5. Testing

Tests live in `tests/` and use **pytest** (no network access needed). Add tests for new logic and make sure everything passes before creating a pull request:

```bash
python -m pytest
```

### 6. Pull Requests

1. Push your branch to your fork.
2. Open a pull request (PR) against the `main` branch of this repository.
3. Provide a clear description of what your PR changes and why.
4. Respond to any feedback and make updates if necessary.

### 7. Reporting Issues

If you find a bug or want to suggest a feature, please open an issue with a descriptive title and details about the problem.

## Thank You

Your contributions help make this project better and more useful for everyone. We appreciate your time and effort!
