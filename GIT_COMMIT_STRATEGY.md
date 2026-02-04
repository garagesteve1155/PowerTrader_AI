# Git Commit Strategy - Professional Development Workflow

## Commit Philosophy

As a senior developer, commits should be:
- **Atomic**: Each commit represents one logical change
- **Frequent**: Commit working code often, not everything at once
- **Descriptive**: Clear commit messages following conventions
- **Tested**: Code compiles/runs before committing
- **Reviewable**: Changes are small enough to review easily

## Conventional Commits Standard

We follow the Conventional Commits specification:

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Types
- **feat**: New feature
- **fix**: Bug fix
- **docs**: Documentation changes
- **style**: Code style changes (formatting, no logic change)
- **refactor**: Code refactoring (no functional changes)
- **perf**: Performance improvements
- **test**: Adding or updating tests
- **build**: Build system changes (PyInstaller, dependencies)
- **ci**: CI/CD changes (GitHub Actions)
- **chore**: Maintenance tasks (updating .gitignore, etc.)

### Examples

```bash
# Good commits
git commit -m "feat(pyinstaller): add initial spec file for pt_hub"
git commit -m "fix(spec): include matplotlib backend in hidden imports"
git commit -m "docs(mkdocs): configure Dracula theme"
git commit -m "refactor(hub): extract subprocess path resolution logic"
git commit -m "build(deps): add pyinstaller to requirements-dev.txt"

# Bad commits (avoid these)
git commit -m "updates"
git commit -m "fixed stuff"
git commit -m "WIP"
git commit -m "finished everything"
```

## Commit Workflow for This Project

### Phase 1: PyInstaller Setup

**Session 1: Initial PyInstaller test**
```bash
# 1. Install PyInstaller
pip install pyinstaller
git add requirements-dev.txt
git commit -m "build(deps): add pyinstaller for executable builds"

# 2. First build attempt
pyinstaller --onefile --windowed pt_hub.py
# Test, document errors in BUILD_LOG.md
git add BUILD_LOG.md
git commit -m "docs(build): document initial PyInstaller test results"

# 3. Clean up generated files
echo "build/" >> .gitignore
echo "dist/" >> .gitignore
echo "*.spec" >> .gitignore
git add .gitignore
git commit -m "chore(git): ignore PyInstaller build artifacts"
```

**Session 2: Create spec file**
```bash
# 1. Generate and customize spec file
pyi-makespec --onefile --windowed pt_hub.py
# Edit pt_hub.spec, add hidden imports
git add pt_hub.spec
git commit -m "build(pyinstaller): add pt_hub spec with matplotlib backend"

# 2. Test build with spec
pyinstaller pt_hub.spec
# Document results
git add BUILD_LOG.md
git commit -m "docs(build): update results for spec-based build"

# 3. Add more hidden imports
# Edit pt_hub.spec
git add pt_hub.spec
git commit -m "fix(spec): add cryptography hidden imports"
```

**Session 3: Handle data files**
```bash
# 1. Add data files to spec
# Edit pt_hub.spec datas section
git add pt_hub.spec
git commit -m "build(spec): include required data files in bundle"

# 2. Create resource path helper
# Edit pt_hub.py, add get_resource_path()
git add pt_hub.py
git commit -m "refactor(hub): add resource path resolution for bundled app"

# 3. Update other modules
# Edit pt_thinker.py
git add pt_thinker.py
git commit -m "refactor(thinker): use resource path helper for subprocess"
```

### Phase 2: Build Scripts

**Session 4: Windows build script**
```bash
# 1. Create build script
# Create build_windows.bat
git add build_windows.bat
git commit -m "build(windows): add automated build script"

# 2. Test and fix
# Edit build_windows.bat
git add build_windows.bat
git commit -m "fix(build): correct paths in Windows build script"
```

**Session 5: Mac build script**
```bash
# 1. Create build script
# Create build_mac.sh
git add build_mac.sh
git commit -m "build(mac): add automated build script"

# 2. Make executable
chmod +x build_mac.sh
git add build_mac.sh
git commit -m "build(mac): set executable permissions on build script"
```

### Phase 3: GitHub Actions

**Session 6: CI/CD setup**
```bash
# 1. Create workflow file
mkdir -p .github/workflows
# Create .github/workflows/build.yml
git add .github/workflows/build.yml
git commit -m "ci(actions): add initial build workflow"

# 2. Add Windows job
# Edit build.yml
git add .github/workflows/build.yml
git commit -m "ci(actions): configure Windows build job"

# 3. Add macOS job
# Edit build.yml
git add .github/workflows/build.yml
git commit -m "ci(actions): configure macOS build job"

# 4. Add artifact upload
# Edit build.yml
git add .github/workflows/build.yml
git commit -m "ci(actions): add artifact upload to releases"
```

### Phase 4: Documentation

**Session 7: MkDocs setup**
```bash
# 1. Install and initialize
pip install mkdocs mkdocs-dracula-theme
git add requirements-dev.txt
git commit -m "docs(deps): add MkDocs with Dracula theme"

# 2. Create initial structure
mkdocs new .
git add mkdocs.yml docs/
git commit -m "docs(mkdocs): initialize documentation structure"

# 3. Configure Dracula theme
# Edit mkdocs.yml
git add mkdocs.yml
git commit -m "docs(theme): configure Dracula theme"
```

**Session 8: Write content**
```bash
# 1. Create installation guide
# Write docs/getting-started/installation.md
git add docs/getting-started/installation.md
git commit -m "docs(guide): add Windows installation instructions"

# 2. Add screenshots
# Add images to docs/images/
git add docs/images/windows-install-*.png
git commit -m "docs(images): add Windows installation screenshots"

# 3. Continue with Mac instructions
# Edit docs/getting-started/installation.md
git add docs/getting-started/installation.md
git commit -m "docs(guide): add macOS installation instructions"

# 4. Add Mac screenshots
git add docs/images/mac-install-*.png
git commit -m "docs(images): add macOS installation screenshots"
```

## Branching Strategy

### Main branches
- `main`: Production-ready code
- `develop`: Integration branch for features

### Feature branches
```bash
# Create feature branch
git checkout -b feature/pyinstaller-integration
# Work and commit frequently
git commit -m "feat(spec): add initial pt_hub spec file"
git commit -m "fix(spec): resolve matplotlib import issue"
# Push to remote
git push origin feature/pyinstaller-integration
# Create PR when ready
```

### Documentation branches
```bash
# Separate branch for docs
git checkout -b docs/user-guide
# Commit each page separately
git commit -m "docs(guide): add Robinhood setup page"
git commit -m "docs(guide): add training instructions"
git push origin docs/user-guide
```

## Commit Frequency Guidelines

### Too Infrequent (avoid)
```bash
# Bad: One massive commit after 8 hours
git commit -m "feat: add all PyInstaller support and documentation"
```

### Too Frequent (also avoid)
```bash
# Bad: Committing incomplete/broken code
git commit -m "WIP halfway through function"
git commit -m "this doesn't work yet"
```

### Just Right
```bash
# Good: Logical, complete units of work
git commit -m "feat(spec): add pt_hub PyInstaller spec file"
# Test, verify it builds
git commit -m "fix(spec): include matplotlib.backends.backend_tkagg"
# Test, verify matplotlib works
git commit -m "docs(build): document successful build process"
```

## Before Each Commit Checklist

- [ ] Code follows PEP 8 style guidelines
- [ ] Type hints are added for function signatures
- [ ] Docstrings are present for new functions/classes
- [ ] Code has been tested (runs without errors)
- [ ] No debug print statements or commented code
- [ ] Commit message follows conventional commits format
- [ ] Changes are atomic (one logical unit)
- [ ] No unrelated changes mixed in

## Pre-Commit Hooks (Optional)

Consider setting up pre-commit hooks:

```bash
pip install pre-commit
```

Create `.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/psf/black
    rev: 23.12.0
    hooks:
      - id: black
        language_version: python3.12

  - repo: https://github.com/PyCQA/flake8
    rev: 6.1.0
    hooks:
      - id: flake8
        args: [--max-line-length=100]

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.7.1
    hooks:
      - id: mypy
        additional_dependencies: [types-requests]
```

Install hooks:
```bash
pre-commit install
```

## Pull Request Guidelines

### PR Checklist
- [ ] All commits follow conventional commits format
- [ ] Code is tested and working
- [ ] Documentation is updated
- [ ] No merge conflicts
- [ ] Clear PR description explaining changes
- [ ] Screenshots for UI changes
- [ ] Build logs for PyInstaller changes

### PR Description Template
```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Build/CI improvement

## Testing
- [ ] Tested on Windows 10
- [ ] Tested on Windows 11
- [ ] Tested on macOS Intel
- [ ] Tested on macOS Apple Silicon

## Screenshots
[If applicable]

## Checklist
- [ ] Code follows project style guidelines
- [ ] Self-review completed
- [ ] Comments added for complex logic
- [ ] Documentation updated
- [ ] No warnings generated
```

## Example Development Session

Real-world example of a 2-hour coding session:

```bash
# Start
git checkout -b feature/pyinstaller-subprocess-fix

# 0:00 - Identify issue with subprocess paths
# Write test, confirm issue
git add tests/test_subprocess_paths.py
git commit -m "test(subprocess): add failing test for bundled subprocess paths"

# 0:20 - Create helper function
# Add get_resource_path() to utils.py
git add utils.py
git commit -m "feat(utils): add resource path helper for PyInstaller"

# 0:45 - Update pt_hub.py to use helper
# Modify pt_hub.py
git add pt_hub.py
git commit -m "refactor(hub): use resource path helper for subprocess calls"

# 1:10 - Test shows pt_thinker needs update too
# Modify pt_thinker.py
git add pt_thinker.py
git commit -m "refactor(thinker): use resource path helper for subprocess calls"

# 1:30 - Update pt_trader.py
git add pt_trader.py
git commit -m "refactor(trader): use resource path helper for subprocess calls"

# 1:45 - All tests pass, update documentation
git add BUILD_LOG.md
git commit -m "docs(build): document subprocess path resolution solution"

# 2:00 - Push and create PR
git push origin feature/pyinstaller-subprocess-fix
# Create PR on GitHub
```

## Summary

**Key Principles:**
1. Commit early, commit often (but only working code)
2. One logical change per commit
3. Descriptive conventional commit messages
4. Test before committing
5. Keep commits small and reviewable
6. Use feature branches for major work
7. Write commits as if explaining to your future self

**Remember:** Good commit history is documentation of your thought process and makes code review, debugging, and reverting changes much easier.
