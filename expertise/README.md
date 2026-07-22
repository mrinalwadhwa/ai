# Expertise

Expertise organizes guidance by area of work, such as writing documentation, designing architecture, or testing software.

## Organization

`INDEX.md` lists the available expertise files and when to load each one. Each area of work has an overview file named `<area>.md`.

When an area needs more depth, supporting material goes in a matching `<area>/` directory.

```text
expertise/
├── INDEX.md
├── <area>.md
└── <area>/
    ├── <subarea>.md
    └── <subarea>/
```

Load only the expertise relevant to the current work. Each file should stand on its own, and its index entry should say when to read it.
