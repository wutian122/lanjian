# =============================================
# lanjian Frontend Docker 鏋勫缓
# =============================================
# 浣跨敤 Nginx 鎻愪緵闈欐€佹枃浠跺拰鍙嶅悜浠ｇ悊 (鏀寔 SSE 娴佸紡浼犺緭)

FROM node:20-slim AS builder

WORKDIR /app

# 褰诲簳娓呴櫎浠ｇ悊璁剧疆
ENV http_proxy=""
ENV https_proxy=""
ENV HTTP_PROXY=""
ENV HTTPS_PROXY=""
ENV all_proxy=""
ENV ALL_PROXY=""
ENV no_proxy="*"
ENV NO_PROXY="*"

# 閰嶇疆鍥藉唴 npm 闀滃儚婧?
RUN npm config set registry https://registry.npmmirror.com && \
    npm install -g pnpm && \
    pnpm config set registry https://registry.npmmirror.com

# 澶嶅埗渚濊禆鏂囦欢
COPY package.json pnpm-lock.yaml ./

# 澧炲姞缃戠粶瓒呮椂璁剧疆鍜屽苟鍙戞暟闄愬埗锛岄槻姝?ARM 鏋舵瀯鏋勫缓鍗℃
RUN pnpm config set network-timeout 300000 && \
    pnpm config set fetch-retries 5 && \
    pnpm install --no-frozen-lockfile --network-concurrency 1

# 澶嶅埗婧愪唬鐮?
COPY . .

# 馃敟 鏋勫缓鏃朵娇鐢ㄥ崰浣嶇 - 瀹炵幇 Build Once Run Anywhere
ENV VITE_API_BASE_URL=/api/v1

# 鏋勫缓鐢熶骇鐗堟湰
RUN pnpm build

# =============================================
# 鐢熶骇闀滃儚 - 浣跨敤 Nginx (鏀寔 SSE 鍙嶅悜浠ｇ悊)
# =============================================
FROM nginx:1.31.2-alpine

# 澶嶅埗鏋勫缓浜х墿
COPY --from=builder /app/dist /usr/share/nginx/html

# 澶嶅埗 Nginx 閰嶇疆 (鍖呭惈 SSE 鍙嶅悜浠ｇ悊閰嶇疆)
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
