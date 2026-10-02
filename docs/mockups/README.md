# Panel mock-ups

Editable copies of every control panel screen, as a starting point for layout work.

## Where the files are

`current/` (not kept in git; the CI job `panel-ui` regenerates it as the `ui-mockups` artifact):

```
current/
  phone/     390 px wide (pictures at 2x: 780 px)
    live.svg   live.psd   live.png
    mix.svg    mix.psd    mix.png
    media.svg  media.psd  media.png
    system.svg system.psd system.png
  laptop/    1280 px wide (pictures at 2x: 2560 px)
    (the same four screens)
```

Every optional module is switched on, so every card is in the picture.

## Which file for which tool

- **SVG, for Illustrator, Penpot, Figma or Inkscape.** These are vectors with real, editable text. Each section, card and control is its own named group, for example `Card: Autostart` and then `Button: Run it now`. In Illustrator, open the file and look at the Layers panel. In Penpot or Figma, drag the file onto the canvas.
- **PSD, for Photoshop.** One layer group per card. Inside each group, the card's background is at the bottom with one layer per control above it (button, slider, label). Text is pictures here, not editable type. The page background is the bottom layer.
- **PNG** is the flat picture.

## Limits

- The files are made on a Linux machine, so its fonts are measured and pictured. In the SVG the text names the panel's own font list, so on a Mac it shows in the system font and may sit a little wider or narrower than its box.
- Icons drawn with CSS (not SVG) are not in the SVG.
- Shadows and gradients are left out of the SVG; the PSD and PNG have them.
- The tool is `tests/ui/mockups.js`, called by `tests/ui/screenshots.js` when `MOCKUPS` is set.
