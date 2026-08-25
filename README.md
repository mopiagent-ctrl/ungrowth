# Content Pipeline — Personal Development & Growth

AI-agent-gerund contentkanaal. Volledig autonoom: van research tot publicatie.

## Pipeline

```
Research → Writer → Editor → Publisher → Distribute
```

## Structuur

```
agents/          — Hermes skills voor elke rol
blog/            — GitHub Pages site (Jekyll-vrij, pure markdown)
scripts/         — Pipeline orchestration scripts
templates/       — Article templates, social templates
```

## Hoe het werkt

Elke cyclus:
1. Research agent scant trends + bronnen → topic brief
2. Writer agent schrijft artikel (jouw stijl: rauw, direct, bewijsgedreven)
3. Editor agent factcheckt + optimaliseert
4. Publisher agent pushed naar GitHub Pages + Substack
5. Distribute agent deelt op social + mail

Cyclus draait via Hermes cron, dagelijks of om de dag.
