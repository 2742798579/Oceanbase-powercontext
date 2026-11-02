// 用 Edge（本机已装）截图，不需要额外下载浏览器内核
// 用法：node shoot.js [步骤名...]   不带参数则跑全部
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");

const BASE = "http://127.0.0.1:8080";
const OUT = path.join(__dirname, "..", "site", "shots");
const LOG = path.join(__dirname, "..", "data", "shoot.log");

fs.mkdirSync(OUT, { recursive: true });
fs.writeFileSync(LOG, "");

function log(...args) {
  const line = args.join(" ");
  fs.appendFileSync(LOG, line + "\n");
  console.log(line);
}

async function main() {
  const browser = await chromium.launch({ channel: "msedge", args: ["--no-sandbox"] });
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 2,
  });
  const page = await ctx.newPage();
  page.setDefaultTimeout(20000);

  const shot = async (name) => {
    await page.screenshot({ path: path.join(OUT, name + ".png"), timeout: 15000 });
    log("saved", name);
  };

  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForTimeout(2500);
  log("loaded");

  // --- 复习教练 ---
  await page.click('.tab[data-view="coach"]');
  await page.waitForSelector("#cardArea .card-front", { timeout: 20000 });
  await page.waitForTimeout(800);
  await shot("03-coach");

  await page.click("#revealBtn");
  await page.waitForTimeout(500);
  await shot("04-coach-answer");

  if (await page.$("#badBtn")) {
    await page.click("#badBtn");
    await page.waitForTimeout(1800);
    await shot("05-coach-graded");
    log("graded");
  }

  // --- 混合检索 ---
  await page.click('.tab[data-view="search"]');
  await page.waitForTimeout(500);
  // 直接赋值：Playwright 对中文逐字输入时会把字符插到光标前，导致字符串倒序
  await page.evaluate(() => {
    const el = document.querySelector("#queryInput");
    el.value = "为什么 B+ 树插入会变慢";
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
  log("typed =", await page.inputValue("#queryInput"));
  await page.click("#searchBtn");
  await page.waitForSelector(".result", { timeout: 20000 });
  await page.waitForTimeout(1500);
  log("results shown");
  await shot("01-search");

  await page.click("details.panel summary");
  await page.waitForTimeout(700);
  await shot("02-search-sql");

  // --- 学习档案 ---
  await page.click('.tab[data-view="profile"]');
  await page.waitForTimeout(1800);
  await shot("06-profile");

  // --- 资料库 ---
  await page.click('.tab[data-view="library"]');
  await page.waitForTimeout(900);
  await shot("07-library");

  log("done");
  await browser.close().catch(() => {});
  process.exit(0);
}

main().catch((e) => {
  log("ERROR", e && e.message ? e.message : String(e));
  process.exit(1);
});
