import sqlalchemy as sa
import sqlalchemy_utils as sau
from github import Github
from flask import Blueprint, redirect, request, render_template, url_for, abort
from flask_login import current_user
from jinja2 import Markup
from uuid import UUID, uuid4
from srht.database import Base, db
from srht.config import cfg
from srht.validation import Validation
from dispatchsrht.tasks import TaskDef
from dispatchsrht.tasks.github.auth import githubloginrequired, GitHubAuthorization
from dispatchsrht.tasks.github.auth import submit_build
from dispatchsrht.types import Task

_root = "{}://{}".format(cfg("server", "protocol"), cfg("server", "domain"))
_builds_sr_ht = cfg("network", "builds", default=None)
_github_client_id = cfg("github", "oauth-client-id", default=None)
_github_client_secret = cfg("github", "oauth-client-secret", default=None)

class GitHubPRToBuild(TaskDef):
    name = "github_pr_to_build"
    description = Markup('''
        <i class="fa fa-github"></i>
        GitHub pull requests
        <i class="fa fa-arrow-right"></i>
        builds.sr.ht jobs
    ''')
    enabled = bool(_github_client_id
            and _github_client_secret
            and _builds_sr_ht)

    class _GitHubPRToBuildRecord(Base):
        __tablename__ = "github_pr_to_build"
        id = sa.Column(sau.UUIDType, primary_key=True)
        created = sa.Column(sa.DateTime, nullable=False)
        updated = sa.Column(sa.DateTime, nullable=False)
        user_id = sa.Column(sa.Integer,
                sa.ForeignKey("user.id", ondelete="CASCADE"))
        user = sa.orm.relationship("User")
        task_id = sa.Column(sa.Integer,
                sa.ForeignKey("task.id", ondelete="CASCADE"))
        task = sa.orm.relationship("Task")
        repo = sa.Column(sa.Unicode(1024), nullable=False)
        github_webhook_id = sa.Column(sa.Integer, nullable=False)

    blueprint = Blueprint("github_pr_to_build",
            __name__, template_folder="github_pr_to_build")

    @blueprint.route("/webhook/<record_id>", methods=["POST"])
    def _webhook(record_id):
        record_id = UUID(record_id)
        hook = GitHubPRToBuild._GitHubPRToBuildRecord.query.filter(
                GitHubPRToBuild._GitHubPRToBuildRecord.id == record_id
            ).first()
        if not hook:
            return "Unknown hook " + str(record_id), 404
        valid = Validation(request)
        pr = valid.require("pull_request")
        action = valid.require("action")
        if not valid.ok:
            return "Got request, but it has no commits"
        if action not in ["opened", "synchronize"]:
            return "Got update, but there are no new commits"
        head = pr["head"]
        base = pr["base"]
        repo = base["repo"]
        return submit_build(hook, repo, head)

    @blueprint.route("/configure")
    @githubloginrequired
    def configure(github):
        repos = github.get_user().get_repos(sort="updated")
        repos = filter(lambda r: r.permissions.admin and not r.fork, repos)
        existing = GitHubPRToBuild._GitHubPRToBuildRecord.query.filter(
                GitHubPRToBuild._GitHubPRToBuildRecord.user_id ==
                current_user.id).all()
        existing = [e.repo for e in existing]
        return render_template("github/select-repo.html",
                repos=repos, existing=existing)

    @blueprint.route("/configure", methods=["POST"])
    @githubloginrequired
    def _configure_POST(github):
        valid = Validation(request)
        repo = valid.require("repo")
        if not valid.ok:
            return "quit yo hackin bullshit"
        repo = github.get_repo(repo)
        if not repo:
            return "quit yo hackin bullshit"
        task = Task()
        task.name = "{}::github_pr_to_build".format(repo.full_name)
        task.user_id = current_user.id
        task._taskdef = "github_pr_to_build"
        db.session.add(task)
        db.session.flush()
        record = GitHubPRToBuild._GitHubPRToBuildRecord()
        record.id = uuid4()
        record.user_id = current_user.id
        record.task_id = task.id
        record.github_webhook_id = -1
        record.repo = repo.full_name
        db.session.add(record)
        db.session.flush()
        hook = repo.create_hook("web", {
            "url": _root + url_for("github_pr_to_build._webhook",
                record_id=record.id),
            "content_type": "json",
        }, ["pull_request"], active=True)
        record.github_webhook_id = hook.id
        db.session.commit()
        # TODO: redirect to task page
        return redirect("/")
