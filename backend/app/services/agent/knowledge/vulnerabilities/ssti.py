"""
SSTI (Server-Side Template Injection) 服务端模板注入漏洞知识

覆盖 Jinja2/Flask、Django 模板、Tornado 等主流 Python 模板引擎，
以及 Node.js (EJS/Handlebars/Pug) 等场景的模板注入检测与防御。
"""

from ..base import KnowledgeDocument, KnowledgeCategory


SSTI_JINJA2 = KnowledgeDocument(
    id="vuln_ssti",
    title="Server-Side Template Injection (SSTI)",
    category=KnowledgeCategory.VULNERABILITY,
    tags=["ssti", "template-injection", "jinja2", "flask", "django", "rce", "injection"],
    severity="critical",
    cwe_ids=["CWE-94", "CWE-1336"],
    owasp_ids=["A03:2021"],
    content="""
服务端模板注入（SSTI）：用户输入被直接拼接进服务端模板引擎的渲染流程，
攻击者通过模板语法（如 `{{ }}`、`{% %}`）执行任意代码或读取敏感数据。

## 危险模式

### Jinja2 / Flask（最高频）
```python
# 危险 - render_template_string 直接拼接用户输入
from flask import Flask, request, render_template_string
app = Flask(__name__)

@app.route('/greet')
def greet():
    name = request.args.get('name', '')
    # 危险：用户输入进入模板字符串
    return render_template_string(f'<h1>Hello {name}</h1>')

# 危险 - Markup 标记为安全，绕过自动转义
from markupsafe import Markup
return render_template_string('<h1>Hello {{ name }}</h1>', name=Markup(user_input))

# 危险 - from_string / Environment 显式编译用户输入
from jinja2 import Environment
template = Environment().from_string(user_input)
return template.render()
```

### Django 模板
```python
# Django 默认自动转义，但以下写法仍危险
from django.template import Template, Context
# 危险：用用户输入构造 Template
t = Template(f'Hello {user_input}')
return t.render(Context({}))

# 危险：mark_safe 绕过转义
from django.utils.safestring import mark_safe
return render(request, 'page.html', {'name': mark_safe(user_input)})
```

### Tornado
```python
# 危险 - tornado.template 直接拼接
from tornado.template import Template
t = Template(f'<h1>{user_input}</h1>')
return t.generate()
```

### Node.js (EJS / Handlebars / Pug)
```javascript
// 危险 - EJS
const ejs = require('ejs');
const html = ejs.render(`<h1><%= ${userInput} %></h1>`);  // 拼接即危险
// 危险 - Handlebars 编译用户输入
const Handlebars = require('handlebars');
const t = Handlebars.compile(userInput);
```

## 检测要点（Source → Sink）
1. **Source**：`request.args/form/get/json`、`request.GET/POST`、`req.query/body`
2. **Sink**：`render_template_string`、`Template()`、`Environment().from_string`、
   `Markup()`、`mark_safe()`、`ejs.render`、`Handlebars.compile`
3. 关注字符串拼接 `f'...{user}...'` 进入模板的链路
4. 关注 `autoescape=False` 或 `|safe` 过滤器的使用

## 典型 Payload（探测 → 利用）
```
# 探测（确认模板上下文）
{{7*7}}            → 返回 49 说明被模板引擎解析
{{7*'7'}}          → Jinja2 返回 '7777777'，Twig 返回 '49'

# 信息收集
{{config}}         # Flask 配置
{{config.items()}}
{{request.environ}}
{{self}}
{{''.__class__}}   # 走 Python 沙箱逃逸链

# RCE（Jinja2 经典逃逸链）
{{''.__class__.__mro__[2].__subclasses__()}}
{{''.__class__.__mro__[1].__subclasses__()[XXX]('id',shell=True,stdout=-1).communicate()}}
{{config.__class__.__init__.__globals__['os'].popen('id').read()}}
{{lipsum.__globals__['os'].popen('id').read()}}
{{cycler.__init__.__globals__.os.popen('id').read()}}
```

## 安全实践
1. **输入与模板分离**：用户输入作为变量传入，不拼入模板字符串
2. **启用自动转义**：Jinja2 `autoescape=True`、Django 默认开启
3. **沙箱化模板引擎**：使用 `SandboxedEnvironment`，禁用危险属性访问
4. **白名单校验**：对进入模板的输入做严格格式校验

## 修复示例
```python
# 安全 - 变量通过 render 传入，自动转义生效
@app.route('/greet')
def greet():
    name = request.args.get('name', '')
    return render_template_string('<h1>Hello {{ name }}</h1>', name=name)

# 安全 - 使用独立模板文件（推荐）
@app.route('/greet')
def greet():
    name = request.args.get('name', '')
    return render_template('greet.html', name=name)

# 安全 - 沙箱环境
from jinja2.sandbox import SandboxedEnvironment
env = SandboxedEnvironment(autoescape=True)
template = env.from_string('<h1>Hello {{ name }}</h1>')
return template.render(name=user_input)
```
""",
)
