'use strict';
// Internal renderer. The agent invokes create_design_pdf.py, never Node or this file.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

function packageRoot(name) {
  for (const root of require.resolve.paths(name) || []) {
    const candidate = path.join(root, name, 'package.json');
    if (fs.existsSync(candidate)) return fs.realpathSync(path.dirname(candidate));
  }
  throw new Error(`基础镜像缺少 ${name}；需要更新镜像，任务中不能安装`);
}
function within(root, filename) {
  const relative = path.relative(root, filename);
  return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative));
}
const MIME = {'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript',
  '.mjs':'text/javascript','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp',
  '.svg':'image/svg+xml','.woff':'font/woff','.woff2':'font/woff2','.ttf':'font/ttf','.otf':'font/otf'};

async function render(request) {
  const {chromium} = require('playwright-core');
  const libraries = Object.fromEntries(['pagedjs','katex','mermaid'].map(name => [name, packageRoot(name)]));
  const candidates = [process.env.CHROME_BIN, process.env.CHROME_PATH, '/usr/bin/chromium', '/usr/bin/chromium-browser'];
  const executablePath = candidates.find(value => value && fs.existsSync(value));
  if (!executablePath) throw new Error('基础镜像缺少可用 Chromium；检查 CHROME_BIN，任务中不能下载浏览器');
  const sourceRoot = fs.realpathSync(path.dirname(request.input));
  const assetRoot = fs.realpathSync(request.assets);
  const errors = [], blocked = [];
  const nonce = crypto.randomBytes(18).toString('base64');
  const policy = `default-src 'none'; script-src 'nonce-${nonce}'; style-src 'unsafe-inline' https://pdf.local; img-src https://pdf.local data: blob:; font-src https://pdf.local data:; connect-src https://pdf.local; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'`;
  async function inject(page, filename) {
    await page.evaluate(({content, nonce}) => {const script=document.createElement('script'); script.nonce=nonce; script.textContent=content; document.head.append(script);}, {content:fs.readFileSync(filename,'utf8'), nonce});
  }
  const browser = await chromium.launch({headless:true, executablePath,
    args:['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage'], timeout:request.timeout_ms});
  try {
    const context = await browser.newContext({serviceWorkers:'block', acceptDownloads:false});
    const page = await context.newPage();
    page.setDefaultTimeout(request.timeout_ms);
    page.on('pageerror', error => errors.push(error.message.slice(0, 500)));
    page.on('console', message => {
      if (message.type() === 'error') errors.push(message.text().slice(0, 500));
    });
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.protocol === 'data:' || url.protocol === 'blob:') return route.continue();
      let root = sourceRoot, relative = decodeURIComponent(url.pathname).replace(/^\/+/, '');
      if (url.origin !== 'https://pdf.local') {
        blocked.push('外部资源：' + url.hostname); return route.abort();
      }
      if (relative.startsWith('__skill__/')) { root = assetRoot; relative = relative.slice(10); }
      else if (relative.startsWith('__lib__/')) {
        const parts = relative.split('/'); root = libraries[parts[1]]; relative = parts.slice(2).join('/');
      }
      try {
        if (!root) throw new Error('unknown root');
        const target = fs.realpathSync(path.resolve(root, relative));
        if (!within(root, target) || !MIME[path.extname(target).toLowerCase()]) throw new Error('forbidden asset');
        if (fs.statSync(target).size > 25 * 1024 * 1024) throw new Error('asset too large');
        return route.fulfill({body:fs.readFileSync(target), contentType:MIME[path.extname(target).toLowerCase()], headers:{'Content-Security-Policy':policy}});
      } catch { blocked.push('缺失或不允许的本地资源：' + relative.slice(0, 200)); return route.abort(); }
    });
    await page.goto('https://pdf.local/' + encodeURIComponent(path.basename(request.input)), {waitUntil:'load'});
    if (await page.evaluate(() => document.compatMode !== 'CSS1Compat'))
      throw new Error('HTML 需以 <!doctype html> 声明标准模式，否则分页和数学公式无法正确排版');
    // Reject active content in the actual DOM too, after the Python preflight.
    const active = await page.evaluate(() => [...document.querySelectorAll('*')].some(el =>
      ['SCRIPT','IFRAME','OBJECT','EMBED','BASE','FRAME'].includes(el.tagName) ||
      [...el.attributes].some(a => /^on/i.test(a.name) || a.name === 'srcdoc')));
    if (active) throw new Error('HTML 包含主动脚本内容；只允许静态 HTML/CSS/SVG');
    await page.emulateMedia({media:'print'});
    let css = fs.readFileSync(path.join(assetRoot, 'design.css'), 'utf8');
    if (request.page_size === 'LETTER') css = css.replaceAll('210mm','215.9mm').replaceAll('297mm','279.4mm').replaceAll('size: A4','size: Letter');
    // Defaults precede author styles so explicit user typography takes precedence.
    await page.evaluate(css => {const style=document.createElement('style'); style.textContent=css; document.head.prepend(style);}, css);
    if (request.css) await page.addStyleTag({content:fs.readFileSync(request.css,'utf8')});
    const stats = await page.evaluate(() => ({figures:document.querySelectorAll('figure').length,
      tables:document.querySelectorAll('table').length, mermaid:document.querySelectorAll('.mermaid').length,
      math:document.querySelectorAll('.math-inline,.math-display').length}));
    if (stats.mermaid) {
      const unsafe = await page.locator('.mermaid').evaluateAll(nodes => nodes.some(n => /%%\s*\{|^\s*---/m.test(n.textContent)));
      if (unsafe) throw new Error('Mermaid 不允许内嵌配置；主题和安全选项由固定渲染器设置');
      await inject(page, path.join(libraries.mermaid,'dist/mermaid.min.js'));
      await page.evaluate(async () => {
        window.mermaid.initialize({startOnLoad:false, securityLevel:'strict', theme:'neutral', maxTextSize:50000,
          flowchart:{htmlLabels:false}, suppressErrorRendering:true});
        await window.mermaid.run({querySelector:'.mermaid'});
      });
    }
    if (stats.math) {
      await page.addStyleTag({url:'https://pdf.local/__lib__/katex/dist/katex.min.css'});
      await inject(page, path.join(libraries.katex,'dist/katex.min.js'));
      await page.evaluate(() => {
        for (const el of document.querySelectorAll('.math-inline,.math-display')) {
          window.katex.render(el.textContent, el, {displayMode:el.classList.contains('math-display'), throwOnError:true,
            trust:false, maxExpand:1000, maxSize:30, strict:'warn'});
        }
      });
    }
    await page.evaluate(async () => {
      await document.fonts.ready;
      await Promise.all([...document.images].map(img => img.complete ? Promise.resolve() : new Promise(resolve => {img.onload=img.onerror=resolve;})));
    });
    const broken = await page.evaluate(() => [...document.images].filter(img => !img.naturalWidth).length);
    if (broken || blocked.length || errors.length) throw new Error('资源加载失败：' + [...new Set([...blocked,...errors]), ...(broken ? [`${broken} 张图片不可读`] : [])].join('；'));
    await page.evaluate(() => {window.PagedConfig={auto:false};});
    await inject(page, path.join(libraries.pagedjs,'dist/paged.polyfill.js'));
    const total = await page.evaluate(async timeout => {
      const flow = await Promise.race([window.PagedPolyfill.preview(), new Promise((_,reject) =>
        setTimeout(() => reject(new Error('分页超时，未输出 PDF')), timeout))]);
      await document.fonts.ready;
      return flow.total;
    }, request.timeout_ms);
    if (!Number.isInteger(total) || total < 1 || total > 200) throw new Error('PDF 页数需在 1–200 之间');
    if (errors.length || blocked.length) throw new Error([...errors,...blocked].slice(0,8).join('；'));
    const pages = await page.evaluate(() => [...document.querySelectorAll('.pagedjs_page')].map((page, index) => {
      const area = page.querySelector('.pagedjs_page_content');
      const rect = area.getBoundingClientRect(), issues=[];
      for (const el of area.querySelectorAll('table,figure,img,svg,pre,.math-display,h1,h2,h3,p')) {
        const box=el.getBoundingClientRect();
        if (box.width && (box.left < rect.left-2 || box.right > rect.right+2 || el.scrollWidth > el.clientWidth+3))
          issues.push({tag:el.tagName.toLowerCase(),text:el.textContent.trim().slice(0,60)});
      }
      return {page:index+1,characters:area.innerText.trim().length,visuals:area.querySelectorAll('img,svg').length,overflows:issues.slice(0,10)};
    }));
    if (pages.length !== total) throw new Error('分页尚未完成：DOM 页数不一致');
    if (pages.some(p => p.overflows.length)) throw new Error('检测到内容横向溢出：' + JSON.stringify(pages.filter(p=>p.overflows.length)));
    await page.pdf({path:request.output, printBackground:true, preferCSSPageSize:true, tagged:true, scale:1,
      timeout:request.timeout_ms});
    return {ok:true,engine:'chromium+pagedjs',offline:true,page_count:total,content:stats,pages,
      warnings:pages.filter(p => !p.characters && !p.visuals).map(p=>`第 ${p.page} 页可能为空白，需要视觉检查`)};
  } finally {await browser.close();}
}
(async () => {
  try {const result=await render(JSON.parse(fs.readFileSync(process.argv[2],'utf8'))); process.stdout.write(JSON.stringify(result));}
  catch(error) {process.stdout.write(JSON.stringify({ok:false,error:String(error.message||error)})); process.exitCode=1;}
})();
