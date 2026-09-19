# Brand prompts for designers

English | [简体中文](brand_prompts.CN.md)

Create a brand at `/create`, then open Chat on the collection page to design images.
The brand form has six fields. Name and description are required; the rest are
optional free text. Save edits as a new version and start a new chat to use it.
Existing chats keep the brand version they started with.

| Field | Design guidance |
| --- | --- |
| Brand name | Fieldwork Studio |
| Brand description | Useful tools for curious designers; clear and considered visual communication |
| Brand colors palette | Blue #2255CC, ivory #FAF8F0; blue for emphasis |
| Personality | premium, calm, technical, optimistic |
| Typography | Humanist sans serif, generous spacing, short headings |
| Illustration style | Geometric shapes, fine outlines, subtle paper texture |

## Edit the prompt file

Open [image_agent.yaml](../backend/app/prompts/image_agent.yaml). There are six blocks:

- `system`: common behavior and instruction priority.
- `brand`: the six-field brand brief, populated from the saved profile.
- `chat`: when to reply, compile an image task, or explain execution results.
- `plan`: creative planning for the legacy run API.
- `generate`: how Nano Banana translates the brand into visual choices.
- `evaluate`: how the result is assessed and when a revision is requested.

Edit the text inside each YAML `|` block and keep its indentation. The `brand`
block must retain all six placeholders: `$name`, `$description`, `$colors`,
`$personality`, `$typography`, `$illustration_style`. Empty values render as
“Not specified”. The application substitutes values once; text entered by users
cannot introduce new template substitutions. Other blocks contain literal text.

Current user requirements override conflicting brand defaults. Typography guides
requested lettering; it does not request adding text or logos to every image.

The provider reads this YAML when initialized. Restart the API and worker after
editing prompts so the change applies consistently. For a deployed service,
redeploy to ship the updated file. The YAML is packaged under `app/` alongside
backend code and is included in the Modal service image. The sandbox receives the
compiled task; provider credentials and template execution remain in the gateway.

Run `make agent-test` and `make lint` after edits. These tests check template
validity, brand persistence, and provider request formats without paid generation.
