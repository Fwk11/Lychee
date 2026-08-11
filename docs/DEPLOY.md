# 免费部署 lychee 到自定义子域名

目标：不花一分钱，把站点挂到 `lychee.<平台后缀>`（若 `lychee` 被占用则用兜底名）。

> 前提：`scripts/build_static.py` 已把站构建到 `output/dist/`（含 `index.html` + `data/` + js/css）。

---

## 方案 A：Vercel（推荐，最简）

1. 注册免费账号 https://vercel.com （可用 GitHub 登录）
2. 装 CLI：`npm i -g vercel`
3. 在项目根目录执行：
   ```bash
   vercel login        # 浏览器授权
   vercel --prod       # 部署
   ```
4. 部署时 Project Name 填 **`lychee`** → 期望得到 `lychee.vercel.app`
   - ⚠️ `lychee` 是常见英文词，Vercel 子域全球预留，**已被占用**。实测最终拿到的是
     `lychee-jade.vercel.app`（Vercel 自动加随机后缀）。`lychee` 这个纯净子域拿不到。
   - 若想换更短的子域，可在 Vercel 控制台 Domains 里改，或用兜底名重建：
     `lychee-app` · `lychee32732` · `lychee-agent`
5. 部署成功后控制台会显示实际地址（如 `lychee-jade.vercel.app`）。

### ⚠️ 已踩坑（务必照做）
- **`vercel.json` 里绝不能写 catch-all 路由**（`{src:"/(.*)",dest:"/index.html"}`）。
  它会把 `/data/*.json` 也重定向成首页 HTML，导致静态站音乐推荐变空白。
  正确做法：`vercel.json` 只设 `buildCommand:""` + `outputDirectory:"output/dist"` + `framework:null`，
  **不要写 routes**。本仓库已如此配置。
- **`buildCommand` 必须设为空字符串 `""`**，不能是 `null`——`null` 会让 Vercel 回退去探测
  FastAPI 框架而卡住/报错。
- **加 `.vercelignore`**（`*` + `!output/dist` + `!vercel.json`）只传 `output/dist/`，否则会把整个仓库
  （含 `data/novel` 音频、`src`、`node_modules`）打包成 240MB 上传，每次部署要好几分钟。
- **非交互部署命令**（跳过所有会卡住的提问）：
  ```bash
  vercel --prod --name lychee --yes
  ```
  （`--name` 已 deprecated 但可用；会直接读 `vercel.json`，不再问 Build Command）

## 方案 B：Netlify

1. 注册免费账号 https://app.netlify.com
2. 装 CLI：`npm i -g netlify-cli`
3. 执行：
   ```bash
   netlify login
   netlify deploy --prod --dir=output/dist
   ```
4. 首次会让你选 team + 填 site name，填 **`lychee`**（被占则 `lychee32732`）→ 得到 `lychee.netlify.app`

## 方案 C：Cloudflare Pages

1. 注册免费账号 https://pages.cloudflare.com
2. 连 Git 仓库或直接拖拽 `output/dist/` 上传
3. Project name 填 **`lychee`**（被占则 `lychee32732`）→ 得到 `lychee.pages.dev`

---

## 说明

- 三个方案**全部免费**，子域名后缀分别为 `.vercel.app` / `.netlify.app` / `.pages.dev`。
- 部署动作需要你本人登录对应平台账号（我无法替你登录），所以最后一步在你本地跑即可。
- 若日后想换回真正的 `lychee32732.com`，按上一轮步骤买域名 + 在平台绑自定义域名即可。