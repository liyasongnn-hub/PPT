前端嵌套iframe无法全屏问题
在 iframe / 微前端中，全屏失败但函数已触发，本质通常是 浏览器权限或用户手势被拦截。
下面给你一个**简洁版总结**，把 iframe 全屏最常见的问题和结论一次说清楚，适合直接放到文档或 PPT 里 👇

---

## iframe 的全屏问题（简单总结）

在 iframe / 微前端中，**全屏失败但函数已触发**，本质通常是 **浏览器权限或用户手势被拦截**。

### 一、最常见两大原因

---

### A. iframe 没有被允许全屏权限（权限问题）

当应用运行在 iframe 内时，**全屏权限由宿主页面决定**。

#### 1️⃣ 必须显式允许 fullscreen

```html
<iframe
  src="..."
  allow="fullscreen"
  allowfullscreen
></iframe>
```

#### 2️⃣ 使用了 sandbox 时更容易踩坑

如果 iframe 使用了 `sandbox`，通常还需要：

```html
<iframe
  src="..."
  sandbox="
    allow-scripts
    allow-same-origin
    allow-popups
    allow-popups-to-escape-sandbox
  "
  allow="fullscreen"
></iframe>
```

> ⚠️ 常见影响：

* Fullscreen API 直接被拒绝
* 视频“全屏 / 放映”按钮点击无反应
* console 无明显报错，只是 silently fail

---

### B. “用户手势链”断了（时序问题）

**浏览器要求 `requestFullscreen()` 必须在“用户手势”同步触发链路中执行。**

以下情况都会导致失败：

```js
button.onclick = () => {
  setTimeout(() => {
    el.requestFullscreen() // ❌ 失败
  }, 0)
}
```

```js
await someAsync()
el.requestFullscreen() // ❌ 失败
```

```js
nextTick(() => {
  el.requestFullscreen() // ❌ 失败
})
```

#### ✅ 正确方式

```js
button.onclick = () => {
  el.requestFullscreen() // ✅ 同步、立即执行
}
```

> 一旦进入 async / Promise / setTimeout / nextTick
> 👉 浏览器就认为 **不是用户主动行为**

---

## 二、微前端 / iframe 场景的典型现象

* TrainPPTAgent 的「放映」按钮触发了函数
* 但 **全屏 / 播放被浏览器拦截**
* 本地直接打开 OK，嵌入宿主页面就失败
* iframe 内看不到明显报错

---

## 三、一句话结论（好记版）

> **iframe 全屏失败 = 权限没开 或 用户手势断了**

---
