# Product, content, and data

## Inputs before writing anything
- **Product:** what it actually does, established from its code or the product in use, in `docs/product-truth.md`.
  Anything the product does not do belongs neither on screen nor in the voice. An unfinished capability is not
  shown as available.
- **Source material:** deck, notes, or the user's script. The text and its order come from this material. The
  user's notes establish what to say and in what order; refine the delivery.
- **Audience and goal:** who it addresses, what they must understand, and what they must do at the end (one CTA).
- **References:** videos the user likes and why. Download them as contact sheets and break them into techniques;
  borrow the craft, not the content.
- **Brand:** the logo files as provided, without redrawing; colors; typefaces licensed for commercial use.
- **Formats, duration, preview channel,** and whether the user wants to receive previews as they are produced.
- **Paid APIs:** which ones, with which key and which cap. No written cap, no spending.

## The product on screen
- Render it from its real code, with coherent demo fixtures labeled as such: a "sorted" list is sorted, a total adds
  up, and the same number does not contradict itself within one frame.
- Use icons as they appear in the product: outlined, in the text color or muted.
- A real-world element (a phone, a notification, a spreadsheet) looks like the real thing, not an invented card.
- Demo names do not identify real people, clients, or fields.
- **Example series with physics:** draw each series with the real behavior of its variable (daily cycle, noise,
  range, response to an event such as rain), preferably from a measured day. Two series never mirror each other
  (one is the inverse of the other) or move in lockstep (rising and falling together): real variables only partly
  covary, with lags. A specialist notices at a glance and stops believing the rest of the video.

## Photos and real objects
- Use original photos with permission. Do not repeat the same photo in consecutive shots.
- Never draw a real object with primitives. If no photo is available, use a professional asset, with the user's
  approval if it costs money.

## Geographic data and satellite imagery
- Use sources with licenses that allow commercial use, verified in `collection.json` or on the official page before
  downloading anything. Verified examples:
  - TRAZO agricultural plots (WRI and ASU), CC BY 4.0, including Córdoba and Chaco;
  - Sentinel-2 L2A through Element84 Earth Search, Copernicus license, credit "Contains modified Copernicus Sentinel
    data <year>".
- Forbidden: imagery from Google, Esri, or Mapbox, and noncommercial mosaics (such as EOX's s2cloudless).
- Generate assets with a repository script, with CLI parameters and demo names, and add the credit to
  `content/credits.md`, the end card, and the video description.

## Licenses for material used in the film
The delivery README lists the license of everything used in the film: music, effects, typefaces, images, data,
logos, and third-party code.
