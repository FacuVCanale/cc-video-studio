// Delete the temporary Remotion bundle created by this process on exit.
// `bundle()` without outDir leaves <tmp>/remotion-webpack-bundle-* (~170 MB) behind. Call this with the serveUrl
// returned by bundle(): it acts only when the directory is directly under the system temp directory and has that
// prefix, so it never deletes a bundle intentionally stored in the project.
//
//   import {dropBundleOnExit} from './drop_bundle_on_exit.mjs';
//   const serveUrl = await bundle({entryPoint, webpackOverride});
//   dropBundleOnExit(serveUrl);
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const dropBundleOnExit = (serveUrl, {prefix = 'remotion-webpack-bundle-'} = {}) => {
  const dir = path.resolve(serveUrl);
  if (!path.basename(dir).startsWith(prefix) || path.dirname(dir) !== path.resolve(os.tmpdir())) return false;
  process.on('exit', () => fs.rmSync(dir, {recursive: true, force: true}));
  return true;
};
