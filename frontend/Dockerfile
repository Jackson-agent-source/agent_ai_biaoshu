# ============================================================
# 前端镜像（多阶段构建）
#
#   阶段一：用 Node 把 Vue 项目构建成静态文件
#   阶段二：把静态文件塞进 nginx，顺便由它反代后端
#
# 最终镜像里没有 Node，也没有 node_modules —— 只有几十 MB 的静态文件和 nginx。
# 构建产物是纯静态的，不需要任何运行时环境。
#
# ★★ 这个容器**只监听 80、只讲明文 HTTP**，不碰 TLS。
#    对外的 HTTPS 由宿主机上的宝塔 nginx 终结，宝塔再反代到
#    127.0.0.1:8080。为什么是宝塔而不是让这个容器独占 80/443：
#    那台机器上还有别的网站在宝塔上，抢端口会把它们一起搞挂。
#    完整说明见 README 12.10。
# ============================================================

# ------------------------------------------------------------------
# 阶段一：构建
# ------------------------------------------------------------------
FROM node:20-alpine AS build

WORKDIR /app

# ★ 让 Node 有足够的内存做类型检查。
#
#   `npm run build` 实际跑的是 `vue-tsc --noEmit && vite build`，
#   vue-tsc 会对整个项目做全量类型检查，加上 Element Plus 的类型定义，
#   峰值内存能超过 1GB。
#
#   不设这个的话，小内存机器上 node 会被系统的 OOM killer 直接杀掉，
#   报错只有孤零零的一行：
#       Killed
#   完全指不出是内存问题，非常容易误判成"代码写错了"而去查代码。
ENV NODE_OPTIONS=--max-old-space-size=1536

# ★ VITE_* 变量是**构建时静态内联**进 JS 的，运行期改环境变量没有用。
#   所以必须在这里用 ARG 传进来，而且要在 npm run build 之前。
#
#   默认值给了相对路径 /api/v1（前后端同源部署）。
#   就算构建参数因为某种原因没传进来，这个默认值也是对的。
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# 先只拷依赖清单：只要依赖没变，改业务代码就不会重新装一遍 node_modules
COPY package.json package-lock.json ./

# ★ 用 ci 而不是 install：ci 严格按 package-lock.json 装，
#   保证每次构建出来的依赖完全一致。install 可能会悄悄升个小版本。
#   （.dockerignore 里**不能**排除 package-lock.json，否则这一步直接失败。）
RUN npm ci

# 再拷源码
COPY . .

RUN npm run build

# ------------------------------------------------------------------
# 阶段二：运行
# ------------------------------------------------------------------
FROM nginx:1.27-alpine

# 清掉官方镜像自带的默认站点配置，换成我们的
RUN rm -f /etc/nginx/conf.d/default.conf

# ★ 放进 templates/ 而不是 conf.d/，这是官方镜像的一个约定：
#   容器启动时 docker-entrypoint.d/20-envsubst-on-templates.sh 会
#   把 /etc/nginx/templates/*.template 里的 ${VAR} 用环境变量替换掉，
#   生成到 /etc/nginx/conf.d/ 下（去掉 .template 后缀）。
#
#   我们需要它来注入 ADMIN_URL_PATH —— 后台路径是可配置的
#   （README 里建议改成一串随机字符），而 nginx 配置里没法直接读环境变量。
#
#   envsubst 只替换**环境里真实存在**的变量名，所以 $uri / $host
#   / $http_x_forwarded_proto 这些 nginx 自己的变量会原样保留。
COPY nginx.conf /etc/nginx/templates/default.conf.template

# ★ 默认值必须给。
#   没给的话，如果 compose 那边也没传，${ADMIN_URL_PATH} 会原样留在配置里，
#   nginx 会把它当成一个叫 admin_url_path 的变量，启动直接失败：
#       unknown "admin_url_path" variable
#   给了默认值就永远能替换成功。
#   （这是个"响亮的失败"而不是静默错误，但仍然没必要让用户撞上。）
ENV ADMIN_URL_PATH=admin/

# 只把构建产物搬过来，Node 和源码都不进最终镜像
COPY --from=build /app/dist /usr/share/nginx/html

# ★ 只有 80。443 不在这里 —— TLS 由宿主机上的宝塔终结（见文件开头）。
EXPOSE 80

# ★ 打 /healthz 而不是 /。
#   /healthz 是一个直接 return 200 的 location，不碰后端、不读磁盘，
#   容器一起来就能通过。打 / 会走 try_files 去读 index.html，
#   镜像没构建好时健康检查的失败原因会和"页面挂了"混在一起。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qS --spider http://127.0.0.1/healthz >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
