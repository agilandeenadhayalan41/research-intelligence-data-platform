import assert from 'node:assert/strict';
import {describe, test} from 'node:test';

import {
  PNG_MAX_PIXELS,
  PNG_MAX_SIDE,
  PNG_MIN_LONG_SIDE,
  PNG_SCALE,
  fileNameFor,
  parseViewBox,
  rasterSize,
  renameSvgId,
  svgDataUrl,
  svgToStandaloneString,
} from '../src/lib/download.mjs';

// A trimmed-down Mermaid flowchart as XMLSerializer writes it in the page.
const MERMAID_SVG =
  '<svg id="mermaid-svg-42" width="100%" xmlns="http://www.w3.org/2000/svg" class="flowchart" ' +
  'style="max-width: 812.5px; background-color: transparent;" viewBox="-8 -8 812.5 474" ' +
  'role="graphics-document document" aria-roledescription="flowchart-v2">' +
  '<style>#mermaid-svg-42{font-family:"trebuchet ms",verdana,arial,sans-serif;}</style>' +
  '<g><marker id="mermaid-svg-42_flowchart-v2-pointEnd"/>' +
  '<path marker-end="url(#mermaid-svg-42_flowchart-v2-pointEnd)" style="fill: none;"/>' +
  '<foreignObject width="80" height="24"><div xmlns="http://www.w3.org/1999/xhtml" style="display: table-cell;">A &amp; B<br/></div></foreignObject>' +
  '</g></svg>';

describe('fileNameFor: images', () => {
  test('uses the file name of the URL', () => {
    assert.equal(fileNameFor({src: '/img/blog/publishing-flow.svg', alt: 'x'}), 'publishing-flow.svg');
    assert.equal(fileNameFor({src: 'https://example.org/a/b/Photo.JPG?w=200#top'}), 'Photo.jpg');
    assert.equal(fileNameFor({src: 'images/chart.png'}), 'chart.png');
  });

  test('drops the Docusaurus content hash of a built asset', () => {
    assert.equal(fileNameFor({src: '/assets/images/publishing-flow-8d1e2c3a4b5f6071.svg'}), 'publishing-flow.svg');
    assert.equal(fileNameFor({src: '/base/assets/images/flow-3f2a9c1b.png'}), 'flow.png');
    // Outside /assets/ a hyphenated suffix is part of the name.
    assert.equal(fileNameFor({src: '/img/release-2026abcd.png'}), 'release-2026abcd.png');
  });

  test('decodes and sanitises the name', () => {
    assert.equal(fileNameFor({src: '/img/my%20chart%20(final).png'}), 'my-chart-final.png');
    assert.equal(fileNameFor({src: '/img/caf%C3%A9.png'}), 'cafe.png');
    assert.equal(fileNameFor({src: '/img/bad%ZZname.png'}), 'bad-ZZname.png');
    assert.equal(fileNameFor({src: '/img/..%2F..%2Fetc%2Fpasswd'}), 'passwd');
  });

  test('falls back to the slugified alt text plus the original extension', () => {
    assert.equal(fileNameFor({src: '/assets/images/12345678-abcdef12.png', alt: 'Data flow, v2'}), 'data-flow-v2.png');
    assert.equal(fileNameFor({src: 'data:image/png;base64,iVBORw0KGgo=', alt: 'Inline chart'}), 'inline-chart.png');
    assert.equal(fileNameFor({src: 'data:image/svg+xml,%3Csvg%3E', alt: ''}), 'image.svg');
    assert.equal(fileNameFor({src: 'blob:https://example.org/1234', alt: 'Upload'}), 'upload');
    assert.equal(fileNameFor({src: 'https://example.org/', alt: 'Home'}), 'home');
  });

  test('rejects input without a src or a page path', () => {
    assert.throws(() => fileNameFor({}), TypeError);
  });
});

describe('fileNameFor: diagrams', () => {
  test('page slug, "diagram" and the 1-based index', () => {
    assert.equal(
      fileNameFor({pagePath: '/overview/how-it-works', index: 1, extension: 'svg'}),
      'overview-how-it-works-diagram-1.svg',
    );
    assert.equal(
      fileNameFor({pagePath: '/architecture/platform-architecture/', index: 3, extension: '.PNG'}),
      'architecture-platform-architecture-diagram-3.png',
    );
    assert.equal(fileNameFor({pagePath: '/', index: 2, extension: 'svg'}), 'home-diagram-2.svg');
    assert.equal(fileNameFor({pagePath: '/adr/?x=1#y', index: 1, extension: 'svg'}), 'adr-diagram-1.svg');
  });

  test('rejects a bad index or extension', () => {
    assert.throws(() => fileNameFor({pagePath: '/a', index: 0, extension: 'svg'}), RangeError);
    assert.throws(() => fileNameFor({pagePath: '/a', index: 1.5, extension: 'svg'}), RangeError);
    assert.throws(() => fileNameFor({pagePath: '/a', index: 1, extension: 'sv/g'}), RangeError);
  });
});

describe('parseViewBox', () => {
  test('parses spaces and commas', () => {
    assert.deepEqual(parseViewBox('-8 -8 812.5 474'), {x: -8, y: -8, width: 812.5, height: 474});
    assert.deepEqual(parseViewBox('0,0,10,20'), {x: 0, y: 0, width: 10, height: 20});
  });

  test('null when missing, malformed or empty', () => {
    assert.equal(parseViewBox(null), null);
    assert.equal(parseViewBox(''), null);
    assert.equal(parseViewBox('0 0 10'), null);
    assert.equal(parseViewBox('0 0 0 10'), null);
    assert.equal(parseViewBox('0 0 a 10'), null);
  });
});

describe('svgToStandaloneString', () => {
  test('adds the XML declaration and explicit pixel size, keeps the drawing', () => {
    const out = svgToStandaloneString(MERMAID_SVG);
    assert.ok(out.startsWith('<?xml version="1.0" encoding="UTF-8"?>\n<svg '));
    const open = /<svg [^>]*>/.exec(out)[0];
    assert.match(open, /xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
    assert.match(open, / width="812\.5" height="474"/);
    assert.match(open, / viewBox="-8 -8 812\.5 474"/);
    assert.doesNotMatch(open, /100%/);
    assert.doesNotMatch(open, /max-width/);
    assert.match(open, /style="background-color: transparent"/);
    assert.match(open, /id="mermaid-svg-42"/);
    assert.match(open, /aria-roledescription="flowchart-v2"/);
    assert.equal((out.match(/xmlns="http:\/\/www\.w3\.org\/2000\/svg"/g) ?? []).length, 1);
    assert.ok(out.includes('<style>#mermaid-svg-42{font-family:"trebuchet ms",verdana,arial,sans-serif;}</style>'));
    assert.ok(out.includes('<div xmlns="http://www.w3.org/1999/xhtml" style="display: table-cell;">A &amp; B<br/></div>'));
    assert.ok(out.trimEnd().endsWith('</svg>'));
  });

  test('width, height and a background rect covering the viewBox', () => {
    const out = svgToStandaloneString(MERMAID_SVG, {width: 2437.5, height: 1422, background: '#11161d'});
    assert.match(out, / width="2437\.5" height="1422"/);
    assert.match(out, /<svg [^>]*><rect x="-8" y="-8" width="812\.5" height="474" fill="#11161d"\/><style>/);
  });

  test('an unsafe background is ignored', () => {
    const out = svgToStandaloneString(MERMAID_SVG, {background: '"/><script>alert(1)</script>'});
    assert.doesNotMatch(out, /<rect/);
    assert.doesNotMatch(out, /<script/);
  });

  test('adds the XLink namespace when xlink: is used, and a viewBox from a pixel size', () => {
    const out = svgToStandaloneString('<svg width="40" height="20"><use xlink:href="#a"/></svg>');
    const open = /<svg [^>]*>/.exec(out)[0];
    assert.match(open, /xmlns:xlink="http:\/\/www\.w3\.org\/1999\/xlink"/);
    assert.match(open, / width="40" height="20" viewBox="0 0 40 20"/);
  });

  test('handles an XML declaration, quotes and ">" inside attribute values', () => {
    const out = svgToStandaloneString(
      `<?xml version="1.0"?><svg viewBox="0 0 10 10" data-note='a > b' aria-label="&quot;x&quot;"><g/></svg>`,
    );
    assert.equal((out.match(/<\?xml/g) ?? []).length, 1);
    assert.match(out, /data-note="a &gt; b"|data-note="a > b"/);
    assert.match(out, /aria-label="&quot;x&quot;"/);
    assert.match(out, /<g\/><\/svg>/);
  });

  test('a self-closing root still gets its background', () => {
    const out = svgToStandaloneString('<svg viewBox="0 0 4 2"/>', {background: 'white'});
    assert.match(out, /<svg [^>]*><rect x="0" y="0" width="4" height="2" fill="white"\/><\/svg>/);
  });

  test('rejects markup that is not an <svg>', () => {
    assert.throws(() => svgToStandaloneString('<div></div>'), /not an <svg>/);
    assert.throws(() => svgToStandaloneString(null), TypeError);
  });
});

describe('renameSvgId and svgDataUrl', () => {
  test('renames the id, its CSS rules and url(#...) references', () => {
    const out = renameSvgId(MERMAID_SVG, 'mermaid-svg-42', 'mermaid-svg-42-zoom');
    assert.doesNotMatch(out, /mermaid-svg-42(?!-zoom)/);
    assert.match(out, /id="mermaid-svg-42-zoom"/);
    assert.match(out, /#mermaid-svg-42-zoom\{/);
    assert.match(out, /url\(#mermaid-svg-42-zoom_flowchart-v2-pointEnd\)/);
    assert.equal(renameSvgId('<svg/>', '', 'x'), '<svg/>');
  });

  test('svgDataUrl percent-encodes the markup', () => {
    const url = svgDataUrl('<svg xmlns="http://www.w3.org/2000/svg"># %</svg>');
    assert.ok(url.startsWith('data:image/svg+xml;charset=utf-8,%3Csvg'));
    assert.doesNotMatch(url.slice(30), /[#%](?![0-9A-F]{2})/);
  });
});

describe('rasterSize', () => {
  test('3x the displayed size', () => {
    assert.equal(PNG_SCALE, 3);
    assert.deepEqual(rasterSize({width: 800, height: 450}), {width: 2400, height: 1350, scale: 3});
  });

  test('at least 2000 px on the longest side for a small diagram', () => {
    assert.equal(PNG_MIN_LONG_SIDE, 2000);
    const size = rasterSize({width: 300, height: 500});
    assert.equal(size.height, 2000);
    assert.equal(size.width, 1200);
    assert.ok(size.scale > 3);
  });

  test('never above the canvas limits', () => {
    const wide = rasterSize({width: 9000, height: 300});
    assert.equal(wide.width, PNG_MAX_SIDE);
    const big = rasterSize({width: 5000, height: 4000});
    assert.ok(big.width * big.height <= PNG_MAX_PIXELS * 1.001);
    assert.ok(big.width <= PNG_MAX_SIDE && big.height <= PNG_MAX_SIDE);
    assert.equal(Math.round((big.width / big.height) * 100) / 100, 1.25);
  });

  test('fits the iOS Safari canvas area limit (4096 x 4096)', () => {
    assert.equal(PNG_MAX_PIXELS, 4096 * 4096);
    // A tall diagram shown at 800 x 2400 CSS px: 3x would be 17.3 million pixels.
    const tall = rasterSize({width: 800, height: 2400});
    assert.ok(tall.width * tall.height <= PNG_MAX_PIXELS);
    assert.ok(tall.width >= 2300 && tall.height >= 7000, `${tall.width} x ${tall.height}`);
    assert.equal(Math.round((tall.height / tall.width) * 100) / 100, 3);
    // Rounding never takes the size past a limit.
    for (const size of [{width: 800, height: 2400}, {width: 1333, height: 999}, {width: 5000, height: 4000}]) {
      const {width, height} = rasterSize(size);
      assert.ok(width * height <= PNG_MAX_PIXELS && width <= PNG_MAX_SIDE && height <= PNG_MAX_SIDE, `${width} x ${height}`);
    }
  });

  test('does not depend on devicePixelRatio: same input, same output', () => {
    assert.deepEqual(rasterSize({width: 640, height: 480}), rasterSize({width: 640, height: 480}));
  });

  test('options and invalid sizes', () => {
    assert.deepEqual(rasterSize({width: 100, height: 100}, {scale: 2, minLongSide: 0}), {width: 200, height: 200, scale: 2});
    assert.throws(() => rasterSize({width: 0, height: 10}), RangeError);
    assert.throws(() => rasterSize({width: Number.NaN, height: 10}), RangeError);
    assert.throws(() => rasterSize(null), RangeError);
  });
});
