"""
XSS (跨站脚本) 漏洞知识
"""

from ..base import KnowledgeDocument, KnowledgeCategory


XSS_REFLECTED = KnowledgeDocument(
    id="vuln_xss_reflected",
    title="Reflected XSS",
    category=KnowledgeCategory.VULNERABILITY,
    tags=["xss", "reflected", "javascript", "html", "injection"],
    severity="high",
    cwe_ids=["CWE-79"],
    owasp_ids=["A03:2021"],
    content="""
反射型XSS：恶意脚本来自当前HTTP请求，服务器将用户输入直接反射到响应中。

## 危险模式

### Python/Flask
```python
# 危险 - 直接返回用户输入
@app.route('/search')
def search():
    query = request.args.get('q')
    return f"<h1>搜索结果: {query}</h1>"

# 危险 - 禁用自动转义
return render_template_string(user_input)
return Markup(user_input)
```

### JavaScript/Express
```javascript
// 危险
res.send(`<h1>Hello ${req.query.name}</h1>`);
res.write(req.body.content);
```

### PHP
```php
// 危险
echo "Hello " . $_GET['name'];
print($_POST['content']);
```

## 攻击载荷
```html
<script>alert('XSS')</script>
<img src=x onerror=alert('XSS')>
<svg onload=alert('XSS')>
javascript:alert('XSS')
<body onload=alert('XSS')>
```

## 安全实践
1. 输出编码/HTML转义
2. 使用模板引擎的自动转义
3. Content-Type设置正确
4. 使用CSP头

## 修复示例
```python
# 安全 - 使用escape
from markupsafe import escape
return f"<h1>搜索结果: {escape(query)}</h1>"

# 安全 - 使用模板（自动转义）
return render_template('search.html', query=query)
```
""",
)


XSS_STORED = KnowledgeDocument(
    id="vuln_xss_stored",
    title="Stored XSS",
    category=KnowledgeCategory.VULNERABILITY,
    tags=["xss", "stored", "persistent", "javascript", "database"],
    severity="high",
    cwe_ids=["CWE-79"],
    owasp_ids=["A03:2021"],
    content="""
存储型XSS：恶意脚本被存储在服务器（数据库、文件等），当其他用户访问时执行。

## 危险场景
- 用户评论/留言板
- 用户个人资料
- 论坛帖子
- 文件名/描述
- 日志查看器

## 危险模式
```python
# 危险 - 存储未过滤的用户输入
comment = request.form['comment']
db.save_comment(comment)  # 存储

# 危险 - 显示未转义的内容
comments = db.get_comments()
return render_template_string(f"<div>{comments}</div>")
```

## 检测要点
1. 追踪用户输入到数据库的流程
2. 检查从数据库读取后的输出处理
3. 关注富文本编辑器的处理
4. 检查管理后台的数据展示

## 安全实践
1. 输入时过滤/存储时转义
2. 输出时始终转义
3. 使用白名单HTML标签（如需富文本）
4. 使用DOMPurify等库清理HTML

## 修复示例
```python
# 安全 - 使用bleach清理HTML
import bleach
clean_comment = bleach.clean(comment, tags=['p', 'b', 'i'])
db.save_comment(clean_comment)
```
""",
)


XSS_DOM = KnowledgeDocument(
    id="vuln_xss_dom",
    title="DOM-based XSS",
    category=KnowledgeCategory.VULNERABILITY,
    tags=["xss", "dom", "javascript", "client-side"],
    severity="high",
    cwe_ids=["CWE-79"],
    owasp_ids=["A03:2021"],
    content="""
DOM型XSS：漏洞存在于客户端JavaScript代码，通过修改DOM环境执行恶意脚本。

## 危险源 (Sources)
```javascript
// URL相关
location.href
location.search
location.hash
document.URL
document.referrer

// 存储相关
localStorage.getItem()
sessionStorage.getItem()

// 消息相关
window.postMessage
```

## 危险汇 (Sinks)
```javascript
// 危险 - HTML注入
element.innerHTML = userInput;
element.outerHTML = userInput;
document.write(userInput);
document.writeln(userInput);

// 危险 - JavaScript执行
eval(userInput);
setTimeout(userInput, 1000);
setInterval(userInput, 1000);
new Function(userInput);

// 危险 - URL跳转
location.href = userInput;
location.assign(userInput);
window.open(userInput);
```

## 危险模式
```javascript
// 危险 - 从URL获取并直接使用
const name = new URLSearchParams(location.search).get('name');
document.getElementById('greeting').innerHTML = 'Hello ' + name;

// 危险 - hash注入
const hash = location.hash.substring(1);
document.getElementById('content').innerHTML = decodeURIComponent(hash);
```

## 安全实践
1. 使用textContent代替innerHTML
2. 使用安全的DOM API
3. 对URL参数进行验证
4. 使用DOMPurify清理HTML

## 修复示例
```javascript
// 安全 - 使用textContent
element.textContent = userInput;

// 安全 - 使用DOMPurify
element.innerHTML = DOMPurify.sanitize(userInput);

// 安全 - 创建文本节点
element.appendChild(document.createTextNode(userInput));
```
""",
)


XSS_FRAMEWORK = KnowledgeDocument(
    id="vuln_xss_framework",
    title="Frontend Framework XSS",
    category=KnowledgeCategory.VULNERABILITY,
    tags=["xss", "react", "vue", "angular", "frontend", "dom", "template-injection"],
    severity="high",
    cwe_ids=["CWE-79"],
    owasp_ids=["A03:2021"],
    content="""
前端框架 XSS：React/Vue/Angular 虽默认转义，但开发者使用"不安全 API"或
模板注入型框架时仍会产生 XSS。本模块覆盖框架特有的危险 Sink 与模板注入型 XSS。

## React 危险模式
```jsx
// 危险 - dangerouslySetInnerHTML 直接渲染原始 HTML
function Comment({ html }) {
    return <div dangerouslySetInnerHTML={{ __html: html }} />;
}

// 危险 - href javascript: 协议
<a href={userUrl}>click</a>           // userUrl = "javascript:alert(1)"

// 危险 - eval / new Function / setTimeout(string)
eval(userInput);
```

## Vue 危险模式
```vue
<!-- 危险 - v-html 直接渲染 -->
<div v-html="userContent"></div>

<!-- 危险 - href 绑定 javascript: -->
<a :href="userUrl">click</a>

<!-- 危险 - 动态模板渲染 -->
<component :is="'div'" v-html="userContent" />
```

## Angular 危险模式
```html
<!-- 危险 - [innerHTML] 绑定（Angular 默认会清理，但绕过 DomSanitizer 后危险） -->
<div [innerHTML]="userContent"></div>

<!-- 危险 - bypassSecurityTrust* 绕过清理 -->
constructor(private sanitizer: DomSanitizer) {}
this.safeHtml = this.sanitizer.bypassSecurityTrustHtml(userContent);
<!-- 危险 - 属性绑定注入事件 -->
<a [href]="userUrl">click</a>
```

## 模板注入型 XSS（服务端 / 客户端模板引擎）
| 引擎 | 危险 Sink | 探测 Payload |
|------|-----------|--------------|
| Jinja2 | `render_template_string(f'..{u}..')` | `{{7*7}}` |
| Handlebars | `Handlebars.compile(u)` | `{{7*7}}` |
| EJS | `ejs.render` 模板字符串拼接用户输入 | `<%= 7*7 %>` |
| Pug | `pug.compile(u)` | `#{7*7}` |
| Mustache | `Mustache.render(u)` | `{{7*7}}` |

## DOM XSS Source / Sink 速查
- **Sources**: `location.href/search/hash`, `document.URL`, `document.referrer`,
  `localStorage.getItem`, `window.name`, `postMessage`
- **Sinks**: `innerHTML`, `outerHTML`, `document.write`, `eval`,
  `setTimeout(string)`, `setInterval(string)`, `new Function`,
  `location.href=`, `location.assign`, `window.open`, `element.setAttribute('onclick', ...)`

## 安全实践与最佳防御
1. **优先使用框架默认转义**：React `{}`、Vue `{{ }}`、Angular `{{ }}` 默认安全
2. **避免 dangerouslySetInnerHTML / v-html / [innerHTML]**，确需富文本时用清理库
3. **URL 协议白名单**：校验 href 只允许 http/https，拒绝 `javascript:` `data:`
4. **富文本用 DOMPurify 清理**

## 修复示例
```jsx
// React 安全 - 富文本用 DOMPurify
import DOMPurify from 'dompurify';
<div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(html) }} />

// Vue 安全
<template><div v-html="sanitized"></div></template>
<script>
import DOMPurify from 'dompurify';
export default {
    computed: {
        sanitized() { return DOMPurify.sanitize(this.userContent); }
    }
}
</script>

// 通用 - URL 协议白名单
function safeUrl(url) {
    const allowed = /^https?:\\/\\//i;
    return allowed.test(url) ? url : '#';
}
```
""",
)
