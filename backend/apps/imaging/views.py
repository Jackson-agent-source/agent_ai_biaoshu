"""
配图 API。

路由：
    GET    /api/v1/articles/<article_id>/images          列出配图（只有元数据）
    POST   /api/v1/articles/<article_id>/images          生成一张（同步，30~90 秒）
    POST   /api/v1/articles/<article_id>/images/upload   上传一张实拍图（multipart）
    PATCH  /api/v1/images/<image_id>                     改图注/位置/顺序/封面
    DELETE /api/v1/images/<image_id>                     删除
    GET    /api/v1/images/<image_id>/file                取图片二进制（兜底，见下）

★ 图片本体的**正常**获取路径是 /media/...（nginx 从共享卷直发）。
  上面最后那条 /file 路由是**兜底**，不是主路径 —— 具体原因写在
  ImageFileView 的 docstring 里。

★ "生成"和"上传"是**两条独立的路由**，不是一个 POST 的两种行为。
  理由写在 ArticleImageUploadView 的 docstring 里（错误码、超时、
  前端调用方式全都不同，合在一起会让日志里的失败无从分辨）。

所有写操作都强制带 user=request.user 或校验文章归属，
别人的文章一律 404（而不是 403）—— 连"这个 id 存不存在"都不泄露。
"""

from __future__ import annotations

import logging

from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.articles.models import Article
from apps.common.exceptions import ImageUploadInvalidError
from apps.common.markdown_sections import section_count
from apps.common.responses import success_response
from apps.imaging import image_utils
from apps.imaging.models import GeneratedImage
from apps.imaging.serializers import (
    GeneratedImageSerializer,
    ImageGenerateSerializer,
    ImageUpdateSerializer,
    ImageUploadSerializer,
)
from apps.imaging.services import (
    delete_image,
    generate_image,
    get_article_images,
    update_image,
    upload_image,
)

logger = logging.getLogger(__name__)


def _get_article(request: Request, article_id) -> Article:
    """取文章并确保属于当前用户。

    和 ArticleDetailView._get_article 一样用 404 —— 403 会告诉对方"这个 id 是存在的"。
    """
    return get_object_or_404(Article, id=article_id, user=request.user)


def _get_image(request: Request, image_id) -> GeneratedImage:
    """取配图并确保它属于当前用户。

    过滤条件放在 user 上而不是 article__user：GeneratedImage 自己有冗余的
    user 字段，这样是一次简单的索引命中，不用 join。
    """
    return get_object_or_404(GeneratedImage, id=image_id, user=request.user)


class ArticleImagesView(APIView):
    """GET 列表 / POST 生成一张。"""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, article_id) -> Response:
        article = _get_article(request, article_id)
        images = get_article_images(article)
        return success_response(
            {
                "results": GeneratedImageSerializer(images, many=True).data,
                # ★ 一并把"正文里实际有几个 ## 小节"告诉前端。
                #   前端要用它来渲染位置下拉框，也要用它校验 anchor_index。
                #   由后端算、不从正文里让前端自己数，
                #   是为了保证两边用的是**同一条规则**（含代码块围栏的处理）。
                "section_count": section_count(article.content_markdown or ""),
            }
        )

    def post(self, request: Request, article_id) -> Response:
        """生成一张配图。

        ★ 这里是**同步**的，一个请求出一张图（30~90 秒）。
          前端拿到配图建议后循环调用，逐张显示进度。
          所以这一条要走 nginx（proxy_read_timeout 600s），而 IMAGE_TIMEOUT
          默认 300 秒，留了足够余量。
        """
        article = _get_article(request, article_id)

        serializer = ImageGenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 校验插入位置不超过这篇文章实际的小节数。
        # 多出来的位置会让图片在渲染时无处可插（静默丢失），
        # 不如在这里直接说清楚。
        # 抛 DRF 的 ValidationError 而不是自己拼信封 ——
        # 全局异常处理器会把它转成标准的 {success:false, error:{...}}，
        # 手写返回体迟早会和那个格式对不上。
        total = section_count(article.content_markdown or "")
        if data["anchor_index"] is not None and data["anchor_index"] > total:
            raise ValidationError(
                f"这篇文章只有 {total} 个小节，插入位置 {data['anchor_index']} 超出范围。"
            )

        image = generate_image(
            article=article,
            user=request.user,
            suggestion=data["suggestion"],
            suggestion_index=data["suggestion_index"],
            prompt=data["prompt"],
            anchor_index=data["anchor_index"],
            caption=data["caption"],
        )
        return success_response(
            GeneratedImageSerializer(image).data, message="配图已生成。", status_code=201
        )


class ArticleImageUploadView(APIView):
    """POST /api/v1/articles/<article_id>/images/upload —— 上传一张实拍图。

    ★ 为什么单独开一条路由，而不是让 ArticleImagesView.post 同时收
      JSON（生成）和 multipart（上传）：

        那个 POST 现在是"生成一张 AI 配图"，语义是"调生图服务"，
        有 30~90 秒的耗时、有额度检查、有 IMAGE_TIMEOUT。
        塞进上传之后，同一个 URL 的两种行为在**错误码、超时、前端调用方式**
        上全都不一样（40009/IMAGE_PROVIDER_ERROR vs IMAGE_UPLOAD_INVALID），
        排查时看到 "POST /articles/x/images 失败" 根本不知道是哪件事。
        两个动作，两条路由。

    ★ 图片本体走 multipart 的 `file` 部分。
      不用 base64 塞进 JSON：base64 会把体积撑大 33%，
      而且 JSON 得整个进内存（multipart 会溢写到临时文件）。
    """

    permission_classes = [IsAuthenticated]
    # ★ 只接受 multipart。默认的 JSONParser 会让一个 Content-Type 写错的
    #   请求变成"file 字段缺失"，而真正的原因是发错了格式 —— 少一种歧义。
    parser_classes = [MultiPartParser]

    def post(self, request: Request, article_id) -> Response:
        article = _get_article(request, article_id)

        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError(
                "没有收到图片文件。请在表单的 file 字段里放一张照片。"
            )

        # ★ 先看 size 再 read()。
        #   UploadedFile.size 是流的长度，不用把内容读进内存就能拿到；
        #   不先判一下的话，一个 200MB 的请求会先被整个读成 bytes 再被拒绝。
        if upload.size == 0:
            raise ValidationError("上传的文件是空的。")
        if upload.size > image_utils.MAX_UPLOAD_BYTES:
            raise ImageUploadInvalidError(
                f"图片太大了（{upload.size // (1024 * 1024)}MB），"
                f"最大支持 {image_utils.MAX_UPLOAD_BYTES // (1024 * 1024)}MB。"
                f"可以先在手机或电脑上把照片压小一点再传。"
            )

        serializer = ImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 插入位置不能超出这篇文章实际的小节数 —— 和生成那条路同一个规则。
        # 超范围的 anchor_index 会让图在渲染时无处可插（静默丢失），
        # 不如在这里直接说清楚。
        total = section_count(article.content_markdown or "")
        if data["anchor_index"] is not None and data["anchor_index"] > total:
            raise ValidationError(
                f"这篇文章只有 {total} 个小节，插入位置 {data['anchor_index']} 超出范围。"
            )

        image = upload_image(
            article=article,
            user=request.user,
            data=upload.read(),
            caption=data["caption"],
            anchor_index=data["anchor_index"],
        )
        return success_response(
            GeneratedImageSerializer(image).data, message="实拍图已上传。", status_code=201
        )


class ImageDetailView(APIView):
    """PATCH / DELETE /api/v1/images/<image_id>"""

    permission_classes = [IsAuthenticated]

    def patch(self, request: Request, image_id) -> Response:
        image = _get_image(request, image_id)

        serializer = ImageUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        image = update_image(image=image, data=serializer.validated_data)
        return success_response(GeneratedImageSerializer(image).data, message="已保存。")

    def delete(self, request: Request, image_id) -> Response:
        image = _get_image(request, image_id)
        delete_image(image=image)
        return success_response(None, message="配图已删除。")


class ImageFileView(APIView):
    """GET /api/v1/images/<image_id>/file —— 兜底取图接口。

    ★★ 正常路径不走这里。★★

        图片由 nginx 从共享卷直接发（frontend/nginx.conf 的
        `location ^~ /media/`），序列化器返回的 file_url 也是那个地址。
        这条路由存在的唯一意义是：当 /media/ 因为卷没挂上、nginx 路由写错
        而 404 时，还有一个**不依赖任何部署配置**的取图入口，
        能在不重新构建镜像的前提下确认"图还在，只是 nginx 那条路不通"。
        排查完就该走 /media/，不要为这个接口做任何优化。

    ★★ 它是 AllowAny，这是**刻意**的，不是忘了加权限。★★

        浏览器的 <img src="..."> 请求**不会带 Authorization 头** ——
        这是浏览器行为，不是我们能改的。所以它没法用 Token 鉴权。
        可选方案只有两个：
            a) 短时效签名 URL（要引入签名逻辑 + 前端配合刷新）
            b) 不可猜的 UUID 路径 + AllowAny
        选了 b：
            · id 是 uuid4，猜不出来（不是自增 id，也不是 uuid1）
            · 这些图是 AI 按配图建议生成的插画，本身不含任何用户隐私
            · 它们**最终就是要公开发到公众号上的**
            · 文章的标题和正文不会因此暴露 —— 这个接口只返回图片字节
        另外加了 X-Robots-Tag: noindex，避免被搜索引擎收录。

    缓存：一条记录的文件一旦写入就不再改动（重新生成是**新建**一条记录 +
    新文件名），所以按 UUID 永久缓存是安全的。
    """

    permission_classes = [AllowAny]
    authentication_classes = []  # 明确不接受认证，省掉一次没意义的 Token 查询

    def get(self, request: Request, image_id) -> HttpResponse:
        # exclude(image_file="") 而不是 exclude(image_file=None)：
        # FileField 的空值是空串不是 NULL（见 models 里的说明）。
        image = GeneratedImage.objects.filter(id=image_id).exclude(image_file="").first()
        if image is None:
            # 不区分"不存在"和"没有图片"，避免变成一个 id 探测接口
            return HttpResponse(status=404)

        try:
            handle = image.image_file.open("rb")
        except (FileNotFoundError, OSError, ValueError):
            # 库里有记录、磁盘上没文件（卷丢了、被手工删了）。
            # 这里给 404 而不是 500 —— 对调用方来说就是"这张图取不到"。
            logger.warning(
                "配图文件缺失 image_id=%s name=%s", image.id, image.image_file.name
            )
            return HttpResponse(status=404)

        # 用 FileResponse 而不是 HttpResponse(read())：前者流式发送，
        # 不会把一张 3MB 的图整个读进内存。
        response = FileResponse(handle, content_type=image.content_type or "image/jpeg")
        response["Cache-Control"] = "public, max-age=31536000, immutable"
        # 不被搜索引擎收录（地址已经是不可猜的，这只是再加一道）
        response["X-Robots-Tag"] = "noindex, noimageindex"
        response["Content-Disposition"] = f'inline; filename="{image.id}.jpg"'
        return response
