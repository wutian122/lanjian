"""
数据库初始化模块
在应用启动时创建超级管理员账户和演示数据

环境变量配置:
  SUPERADMIN_USERNAME     - 超级管理员用户名 (默认: admin)
  SUPERADMIN_PASSWORD     - 超级管理员密码 (P0-4: 必填，无默认。必须满足密码策略：
                            ≥12 位、大小写字母+数字+特殊字符。未设置或弱密码时
                            init_db 会跳过创建并给出日志提示)
  SUPERADMIN_NAME         - 超级管理员姓名 (默认: 超级管理员)
  SUPERADMIN_DEPARTMENT   - 超级管理员部门 (默认: 安全管理部)
  SUPERADMIN_PHONE        - 超级管理员电话 (默认: 13800138000)
  RESET_ALL_USERS         - 是否重置所有用户数据 (默认: false, 设置true强制删除重建)
"""
import os
import json
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete

from app.core.security import get_password_hash, validate_password_policy
from app.models.user import User, UserRole
from app.models.project import Project, ProjectMember
from app.models.audit import AuditTask, AuditIssue
from app.models.analysis import InstantAnalysis
from app.models.user_config import UserConfig
from app.models.audit_rule import AuditRuleSet
from app.models.prompt_template import PromptTemplate

logger = logging.getLogger(__name__)

# 超级管理员配置
SUPERADMIN_USERNAME = os.getenv("SUPERADMIN_USERNAME", "admin")
# P0-4: 不再有默认密码。未设置视为 None，create_super_admin 会跳过创建。
SUPERADMIN_PASSWORD = os.getenv("SUPERADMIN_PASSWORD") or None
SUPERADMIN_NAME = os.getenv("SUPERADMIN_NAME", "超级管理员")
SUPERADMIN_DEPARTMENT = os.getenv("SUPERADMIN_DEPARTMENT", "安全管理部")
SUPERADMIN_PHONE = os.getenv("SUPERADMIN_PHONE", "13800138000")


_MISSING_PASSWORD_HINT = (
    "SUPERADMIN_PASSWORD 未设置。请通过环境变量注入满足密码策略的密码：\n"
    "  1) 生成随机强密码（示例）：\n"
    "     python -c \"import secrets, string; a=string.ascii_letters; d=string.digits; "
    "s='!@#$%^&*'; import random; r=random.SystemRandom(); "
    "print(''.join(r.choice(a+d+s) for _ in range(20)))\"\n"
    "  2) 在 backend/.env 或部署环境中设置 SUPERADMIN_PASSWORD=<强密码>\n"
    "  3) 首次登录后立即在页面上修改密码（is_first_login=True 会触发强制改密）。"
)


async def wipe_all_users(db: AsyncSession) -> None:
    """删除所有用户及其关联数据"""
    logger.warning("⚠️ 正在重置所有用户数据...")

    # 获取所有用户ID
    result = await db.execute(select(User.id))
    user_ids = [row[0] for row in result.all()]

    if not user_ids:
        logger.info("没有用户数据需要删除")
        return

    # 删除关联数据（按依赖顺序）
    await db.execute(delete(AuditIssue).where(AuditIssue.resolved_by.in_(user_ids)))
    subq = select(AuditTask.id).where(AuditTask.created_by.in_(user_ids))
    result = await db.execute(subq)
    task_ids = [row[0] for row in result.all()]
    if task_ids:
        await db.execute(delete(AuditIssue).where(AuditIssue.task_id.in_(task_ids)))
    await db.execute(delete(AuditTask).where(AuditTask.created_by.in_(user_ids)))
    await db.execute(delete(InstantAnalysis).where(InstantAnalysis.user_id.in_(user_ids)))
    await db.execute(delete(AuditRuleSet).where(AuditRuleSet.created_by.in_(user_ids)))
    await db.execute(delete(PromptTemplate).where(PromptTemplate.created_by.in_(user_ids)))
    await db.execute(delete(ProjectMember).where(ProjectMember.user_id.in_(user_ids)))
    await db.execute(delete(Project).where(Project.owner_id.in_(user_ids)))
    await db.execute(delete(UserConfig).where(UserConfig.user_id.in_(user_ids)))
    await db.execute(delete(User).where(User.id.in_(user_ids)))

    logger.warning(f"✓ 已删除 {len(user_ids)} 个用户及其关联数据")


async def create_super_admin(db: AsyncSession) -> User | None:
    """
    创建唯一的超级管理员账户。

    P0-4 变化：
    - 不再使用硬编码默认密码（原来是 123456789）。
    - ``SUPERADMIN_PASSWORD`` 未设置 —— 跳过创建，日志给出注入方式。
    - 密码未通过 :func:`validate_password_policy` —— 同样跳过创建，日志说明未通过的原因。
    - 超级管理员已存在时**不再覆盖**其密码 —— 覆盖逻辑等于每次重启都把用户改过的密码
      重置回环境变量的值，是安全反模式。想要重置密码请显式使用 ``RESET_ALL_USERS=true``。

    Returns:
        创建成功或已存在时返回 User；因缺密码/弱密码跳过时返回 None。
    """
    result = await db.execute(
        select(User).where(User.role == UserRole.SUPER_ADMIN)
    )
    existing = result.scalars().first()

    if existing:
        logger.info(f"超级管理员账户已存在: {existing.username}")
        return existing

    # P0-4: 强制注入密码 —— 未设置直接跳过创建
    if not SUPERADMIN_PASSWORD:
        logger.warning("=" * 60)
        logger.warning("⚠️ 超级管理员未创建：%s", _MISSING_PASSWORD_HINT)
        logger.warning("=" * 60)
        return None

    # P0-4: 校验密码策略 —— 弱密码直接跳过创建
    ok, reason = validate_password_policy(SUPERADMIN_PASSWORD)
    if not ok:
        logger.warning("=" * 60)
        logger.warning(
            "⚠️ 超级管理员未创建：SUPERADMIN_PASSWORD 未通过密码策略校验（%s）。"
            "请重新生成并设置环境变量后再启动。",
            reason,
        )
        logger.warning("=" * 60)
        return None

    # 创建超级管理员
    super_admin = User(
        username=SUPERADMIN_USERNAME,
        hashed_password=get_password_hash(SUPERADMIN_PASSWORD),
        full_name=SUPERADMIN_NAME,
        department=SUPERADMIN_DEPARTMENT,
        phone=SUPERADMIN_PHONE,
        is_active=True,
        is_superuser=True,
        role=UserRole.SUPER_ADMIN,
        is_first_login=True,
        password_history=[get_password_hash(SUPERADMIN_PASSWORD)],
        last_password_change=datetime.now(timezone.utc),
    )
    db.add(super_admin)
    await db.flush()
    logger.info(f"✓ 创建超级管理员: {SUPERADMIN_USERNAME}（首次登录将强制修改密码）")
    return super_admin


async def create_demo_data(db: AsyncSession, user: User) -> None:
    """
    为超级管理员创建演示数据，用于仪表盘展示
    """
    # 检查是否已有演示数据
    result = await db.execute(select(Project).where(Project.owner_id == user.id))
    existing_projects = result.scalars().all()
    if existing_projects:
        logger.info("演示数据已存在，跳过创建")
        return

    logger.info("开始创建演示数据...")
    now = datetime.now(timezone.utc)

    # ==================== 创建演示项目 ====================
    projects_data = [
        {
            "name": "电商平台后端",
            "description": "基于 Spring Boot 的电商平台后端服务，包含用户管理、商品管理、订单处理等模块",
            "source_type": "repository",
            "repository_url": "https://github.com/example/ecommerce-backend",
            "repository_type": "github",
            "default_branch": "main",
            "programming_languages": json.dumps(["Java", "SQL"]),
        },
        {
            "name": "移动端 App",
            "description": "React Native 跨平台移动应用，支持 iOS 和 Android",
            "source_type": "repository",
            "repository_url": "https://github.com/example/mobile-app",
            "repository_type": "github",
            "default_branch": "develop",
            "programming_languages": json.dumps(["TypeScript", "JavaScript"]),
        },
        {
            "name": "数据分析平台",
            "description": "Python 数据分析和可视化平台，集成机器学习模型",
            "source_type": "zip",
            "repository_url": None,
            "repository_type": "other",
            "default_branch": "main",
            "programming_languages": json.dumps(["Python"]),
        },
        {
            "name": "微服务网关",
            "description": "基于 Go 的高性能 API 网关，支持限流、熔断、负载均衡",
            "source_type": "repository",
            "repository_url": "https://gitlab.com/example/api-gateway",
            "repository_type": "gitlab",
            "default_branch": "master",
            "programming_languages": json.dumps(["Go"]),
        },
        {
            "name": "智能客服系统",
            "description": "基于 NLP 的智能客服系统，支持多轮对话、意图识别和知识库问答",
            "source_type": "repository",
            "repository_url": "https://github.com/example/smart-customer-service",
            "repository_type": "github",
            "default_branch": "main",
            "programming_languages": json.dumps(["Python", "JavaScript"]),
        },
        {
            "name": "区块链钱包",
            "description": "多链加密货币钱包，支持 ETH、BTC 等主流币种的存储和转账",
            "source_type": "zip",
            "repository_url": None,
            "repository_type": "other",
            "default_branch": "main",
            "programming_languages": json.dumps(["Rust", "TypeScript"]),
        },
    ]

    projects = []
    for i, pdata in enumerate(projects_data):
        project = Project(
            owner_id=user.id,
            is_active=True,
            created_at=now - timedelta(days=30 - i * 5),
            **pdata
        )
        db.add(project)
        projects.append(project)

    await db.flush()
    logger.info(f"✓ 创建了 {len(projects)} 个演示项目")

    # ==================== 创建审计任务和问题 ====================
    tasks_data = [
        {"project_idx": 0, "status": "completed", "days_ago": 25, "files": 156, "lines": 12500, "issues": 23, "score": 72.5},
        {"project_idx": 0, "status": "completed", "days_ago": 15, "files": 162, "lines": 13200, "issues": 18, "score": 78.3},
        {"project_idx": 0, "status": "completed", "days_ago": 5, "files": 168, "lines": 14100, "issues": 12, "score": 85.2},
        {"project_idx": 1, "status": "completed", "days_ago": 20, "files": 89, "lines": 8900, "issues": 15, "score": 68.7},
        {"project_idx": 1, "status": "completed", "days_ago": 8, "files": 95, "lines": 9500, "issues": 8, "score": 82.1},
        {"project_idx": 1, "status": "completed", "days_ago": 1, "files": 98, "lines": 9800, "issues": 6, "score": 84.5},
        {"project_idx": 2, "status": "completed", "days_ago": 12, "files": 45, "lines": 5600, "issues": 9, "score": 76.4},
        {"project_idx": 2, "status": "completed", "days_ago": 2, "files": 52, "lines": 6200, "issues": 5, "score": 88.9},
        {"project_idx": 3, "status": "completed", "days_ago": 18, "files": 78, "lines": 9200, "issues": 11, "score": 74.8},
        {"project_idx": 3, "status": "failed", "days_ago": 3, "files": 0, "lines": 0, "issues": 0, "score": 0},
        {"project_idx": 4, "status": "completed", "days_ago": 22, "files": 134, "lines": 15800, "issues": 19, "score": 71.2},
        {"project_idx": 4, "status": "completed", "days_ago": 10, "files": 142, "lines": 16500, "issues": 14, "score": 79.6},
        {"project_idx": 4, "status": "completed", "days_ago": 1, "files": 148, "lines": 17200, "issues": 7, "score": 86.8},
        {"project_idx": 5, "status": "completed", "days_ago": 16, "files": 67, "lines": 8400, "issues": 16, "score": 65.3},
        {"project_idx": 5, "status": "completed", "days_ago": 6, "files": 72, "lines": 9100, "issues": 9, "score": 77.5},
    ]

    tasks = []
    for tdata in tasks_data:
        task_time = now - timedelta(days=tdata["days_ago"])
        task = AuditTask(
            project_id=projects[tdata["project_idx"]].id,
            created_by=user.id,
            task_type="full_scan",
            status=tdata["status"],
            branch_name="main",
            total_files=tdata["files"],
            scanned_files=tdata["files"] if tdata["status"] == "completed" else 0,
            total_lines=tdata["lines"],
            issues_count=tdata["issues"],
            quality_score=tdata["score"],
            started_at=task_time,
            completed_at=task_time + timedelta(minutes=5) if tdata["status"] == "completed" else None,
            created_at=task_time,
        )
        db.add(task)
        tasks.append(task)

    await db.flush()
    logger.info(f"✓ 创建了 {len(tasks)} 个审计任务")

    # ==================== 创建审计问题 ====================
    issue_templates = [
        {"type": "security", "severity": "critical", "title": "SQL 注入漏洞", "file": "UserService.java", "line": 45},
        {"type": "security", "severity": "high", "title": "硬编码密钥", "file": "config/secrets.py", "line": 12},
        {"type": "security", "severity": "high", "title": "XSS 跨站脚本攻击风险", "file": "components/Comment.tsx", "line": 78},
        {"type": "security", "severity": "medium", "title": "不安全的随机数生成", "file": "utils/token.go", "line": 23},
        {"type": "bug", "severity": "high", "title": "空指针异常风险", "file": "OrderController.java", "line": 156},
        {"type": "bug", "severity": "medium", "title": "数组越界访问", "file": "DataProcessor.py", "line": 89},
        {"type": "bug", "severity": "low", "title": "未处理的 Promise 拒绝", "file": "api/client.ts", "line": 34},
        {"type": "performance", "severity": "medium", "title": "N+1 查询问题", "file": "ProductRepository.java", "line": 67},
        {"type": "performance", "severity": "low", "title": "不必要的重复渲染", "file": "pages/Dashboard.tsx", "line": 112},
        {"type": "style", "severity": "low", "title": "函数过长，建议拆分", "file": "services/payment.go", "line": 45},
        {"type": "maintainability", "severity": "medium", "title": "重复代码块", "file": "handlers/auth.go", "line": 78},
        {"type": "maintainability", "severity": "low", "title": "缺少错误处理", "file": "utils/http.py", "line": 56},
    ]

    issue_count = 0
    for task in tasks:
        if task.status != "completed" or task.issues_count == 0:
            continue
        num_issues = min(task.issues_count, len(issue_templates))
        for i in range(num_issues):
            template = issue_templates[i % len(issue_templates)]
            issue = AuditIssue(
                task_id=task.id,
                file_path=f"src/{template['file']}",
                line_number=template["line"] + i * 10,
                issue_type=template["type"],
                severity=template["severity"],
                title=template["title"],
                message=template["title"],
                description=f"在文件 {template['file']} 第 {template['line'] + i * 10} 行发现 {template['title']}，这可能导致安全风险或程序异常。",
                suggestion="建议进行代码审查并修复此问题。详细修复方案请参考相关安全规范。",
                status="open" if i % 3 != 0 else "resolved",
                resolved_by=user.id if i % 3 == 0 else None,
                resolved_at=now - timedelta(days=i) if i % 3 == 0 else None,
                created_at=task.created_at,
            )
            db.add(issue)
            issue_count += 1

    await db.flush()
    logger.info(f"✓ 创建了 {issue_count} 个审计问题")

    # ==================== 创建即时分析记录 ====================
    analyses_data = [
        {"lang": "Python", "issues": 3, "score": 75.5, "days_ago": 10},
        {"lang": "JavaScript", "issues": 5, "score": 68.2, "days_ago": 8},
        {"lang": "Java", "issues": 2, "score": 82.1, "days_ago": 6},
        {"lang": "Go", "issues": 1, "score": 91.3, "days_ago": 4},
        {"lang": "TypeScript", "issues": 4, "score": 72.8, "days_ago": 2},
        {"lang": "Python", "issues": 0, "score": 95.0, "days_ago": 1},
    ]

    for adata in analyses_data:
        analysis = InstantAnalysis(
            user_id=user.id,
            language=adata["lang"],
            code_content="# 演示代码\nprint('Hello, World!')",
            analysis_result=json.dumps({"issues": [], "summary": "演示分析结果"}),
            issues_count=adata["issues"],
            quality_score=adata["score"],
            analysis_time=2.5,
            created_at=now - timedelta(days=adata["days_ago"]),
        )
        db.add(analysis)

    await db.flush()
    logger.info(f"✓ 创建了 {len(analyses_data)} 条即时分析记录")

    await db.commit()
    logger.info("✓ 演示数据创建完成")


async def init_db(db: AsyncSession) -> None:
    """
    初始化数据库
    """
    logger.info("开始初始化数据库...")

    # 如果需要重置所有用户（通过环境变量 RESET_ALL_USERS=true）
    if os.getenv("RESET_ALL_USERS", "").lower() in ("true", "1", "yes"):
        await wipe_all_users(db)

    # 创建超级管理员
    super_admin = await create_super_admin(db)

    # 创建演示数据
    if super_admin:
        await create_demo_data(db, super_admin)

    await db.commit()

    # 初始化系统模板和规则
    try:
        from app.services.init_templates import init_templates_and_rules
        await init_templates_and_rules(db)
    except Exception as e:
        logger.warning(f"初始化模板和规则跳过: {e}")

    logger.info("数据库初始化完成")
