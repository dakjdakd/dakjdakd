# AI Coding & Agent Workflow

[Back to home](../README.md) · [中文 profile](./README.zh-CN.md) · [English profile](./README.en.md)

## Positioning

I use AI coding agents as part of an end-to-end product development workflow, not only as code-completion tools.

The goal is to move from an unclear idea to a runnable, reviewable, and deployable prototype while keeping requirements, context, state, and quality checks visible.

## Seven-Step Workflow

1. **Idea definition** — identify the user, core scenario, constraints, and expected result.
2. **Requirement breakdown** — translate the idea into pages, features, data, states, and tasks.
3. **Project scaffold** — establish routes, components, styles, APIs, and a runnable foundation.
4. **Feature building** — use agents for multi-file implementation, component composition, and state logic.
5. **Debugging and verification** — run the real app, inspect errors, screenshots, browser behavior, and edge cases.
6. **Code review** — check naming, duplication, state boundaries, accessibility, type checks, lint, and smoke tests.
7. **Deployment and iteration** — complete build checks, release the prototype, and capture reusable patterns.

## Areas of Practice

- Context engineering and structured output
- Agent task decomposition and state transitions
- Tool calling and permission boundaries
- Multi-agent workflows and review gates
- RAG retrieval, citation, and evaluation
- SSE streaming status and observable execution
- Generative UI and visual design systems

## Tools

### Coding Agents

Claude Code, OpenAI Codex, Cursor, GitHub Copilot, Windsurf, Trae, Cline, Roo Code, Aider, and Gemini CLI.

### AI App Builders

Lovable, Bolt.new, v0, Replit Agent, Dify, and n8n.

## Engineering Principles

- Prefer a clear workflow over a vague prompt.
- Keep evidence and sources visible.
- Make model output structured and reviewable.
- Separate planning, execution, and verification.
- Treat the interface as part of the AI product, not a wrapper around the model.
