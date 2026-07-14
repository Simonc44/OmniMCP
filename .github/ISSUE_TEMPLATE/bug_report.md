name: Bug Report
description: Report a bug or unexpected behavior in OmniMCP Router
labels: ["bug"]
body:
  - type: markdown
    attributes:
      value: |
        Thanks for taking the time to report a bug! Please fill out the form below as completely as possible.

  - type: input
    id: version
    attributes:
      label: OmniMCP Version / Commit
      placeholder: "e.g. main@abc1234"
    validations:
      required: true

  - type: dropdown
    id: os
    attributes:
      label: Operating System
      options:
        - Windows
        - Linux
        - macOS
    validations:
      required: true

  - type: input
    id: python
    attributes:
      label: Python Version
      placeholder: "e.g. 3.11.4"
    validations:
      required: true

  - type: textarea
    id: description
    attributes:
      label: Bug Description
      description: A clear and concise description of what the bug is.
    validations:
      required: true

  - type: textarea
    id: reproduce
    attributes:
      label: Steps to Reproduce
      placeholder: |
        1. Configure mcp_router_config.json with...
        2. Run `python router.py --config ...`
        3. Call tool `...`
        4. See error
    validations:
      required: true

  - type: textarea
    id: expected
    attributes:
      label: Expected Behavior
    validations:
      required: true

  - type: textarea
    id: logs
    attributes:
      label: Relevant Logs (mcp_router.log)
      description: Paste relevant lines from `mcp_router.log` here.
      render: text

  - type: textarea
    id: config
    attributes:
      label: mcp_router_config.json (redact secrets)
      render: json
ENDOFFILE

cat > /home/claude/feature_request.md << 'ENDOFFILE'
name: Feature Request
description: Suggest a new feature or improvement for OmniMCP Router
labels: ["enhancement"]
body:
  - type: markdown
    attributes:
      value: |
        Have an idea to make OmniMCP better? Describe it below.

  - type: textarea
    id: problem
    attributes:
      label: Problem / Motivation
      description: What problem does this feature solve? Why do you need it?
    validations:
      required: true

  - type: textarea
    id: solution
    attributes:
      label: Proposed Solution
      description: Describe the feature you'd like to see implemented.
    validations:
      required: true

  - type: textarea
    id: alternatives
    attributes:
      label: Alternatives Considered
      description: Have you tried any workarounds? Are there other approaches?

  - type: dropdown
    id: priority
    attributes:
      label: How important is this to you?
      options:
        - Nice to have
        - Important
        - Blocking my use case
    validations:
      required: true