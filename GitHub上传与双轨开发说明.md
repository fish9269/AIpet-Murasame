# GitHub 推送与双轨开发说明

> 本仓库 = 旧打包客户端（本地长期使用的版本）的**源码级存档**。
> 隐私与体积文件一律未入库（见 `.gitignore`：config.json / data / face_shibie / NapCat / 模型 / venv 等）。
> 原作仓库：https://github.com/sensei142563/AIpet-Murasame （GPL-3.0，本目录派生自它）

## 一、推送到你自己的 GitHub（一次性步骤）

### 1. 设置提交身份（当前为占位身份 developer/dev@localhost）
```bat
cd /d D:\下载\AI桌宠\1\AIpet-Murasame等3项文件\AIpet-Murasame
git config user.name  "你的GitHub用户名"
git config user.email "你的GitHub邮箱"
git commit --amend --reset-author --no-edit   REM 把基线提交的作者改成你自己（推送前执行一次即可）
```

### 2. 在 GitHub 网页新建空仓库
- 仓库名建议：`AIpet-Murasame`（或 `AIpet-Murasame-legacy`）
- **不要**勾选 Add README / .gitignore / license（避免与本地历史冲突）

### 3. 关联远端并推送（HTTPS 方式）
```bat
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```
- 首次推送会弹 GitHub 登录窗；HTTPS 推送现要求 **Personal Access Token（PAT）**：
  GitHub → Settings → Developer settings → Personal access tokens → Generate new token（勾 `repo` 权限）→ 用户名框填用户名、密码框粘贴该 token
- 若用 SSH：先在 `C:\Users\Administrator\.ssh` 生成密钥并加到 GitHub（Settings → SSH keys），然后把 remote 换成 `git@github.com:<用户名>/<仓库名>.git`

### 4. 日常提交
```bat
git add -A
git commit -m "改动说明"
git push
```

## 二、对照原作更新（双轨制，不在本仓库直接 merge）

原作新版本已经**独立克隆**到旁路目录（开发新基线，见文末），本仓库只做旧版存档与差异对照：

```bat
git remote add upstream https://github.com/sensei142563/AIpet-Murasame.git
git fetch upstream          REM 只下载、不改动本仓库任何文件
git diff --stat HEAD upstream/main   REM 粗略看两版差异（无共同祖先，仅供人工参照）
```

## 三、仓库里留了什么 / 没留什么

| 已入库（源码与素材） | 未入库（需本机保留，绝不推送） |
|---|---|
| pcl_launcher/（启动器+主题+插件面板） | config.json（API Key、QQ 号） |
| qq/（离线/活泼/Galgame/成人/记忆分仓等） | data/（记忆/缓存/日志/QQ 数据） |
| plugins/（各插件 plugin.json 与说明） | face_shibie/（人脸照片） |
| pets/（角色源码与立绘素材，语音参考音频除外） | NapCat.Shell.Windows.OneKey/（登录态） |
| 更新日志/（每次改动按日记录） | runtime/ GPT-SoVITS/ F5-TTS_Models/ models/ |
| run*.py main.py api.py tool/ classes/ Live2d/ longtext/ wechat/ | tmp/（exe 备份与构建日志）、_internal*/、AIpet-Murasame.exe |

> ⚠ 换电脑/清空目录前：先手动备份 `config.json`、`data/`、`face_shibie/`、`NapCat.Shell.Windows.OneKey/config`，
> 这些不在 git 里，删了就没了。

## 四、原作新版开发基线（已另克隆的目录）

```
<本仓库的上级目录>\AIpet-Murasame-upstream\
```
- 它是 `git clone https://github.com/sensei142563/AIpet-Murasame.git` 的最新 main
- 之后的开发建议在**那份新源码**上进行：功能改动按《移植清单_旧版到新版.md》逐步搬，跑通一个提交一个
- 本仓库后续只承担「旧版参考 + 更新日志归档」职责
