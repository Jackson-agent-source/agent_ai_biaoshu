/**
 * 上传前在浏览器里把照片缩小。
 *
 * ★★ 这一步是**优化**，不是正确性的一部分。
 *
 *    真正"处理图片"的地方在后端（apps/imaging/image_utils.py 的
 *    normalize_uploaded_photo）：它才是权威 —— 验明字节是不是图片、
 *    把 EXIF 方向烧进像素、清掉 EXIF（里面有 GPS）、缩到 1600px。
 *    这里做的事和它**故意重叠**，只是为了省用户的时间：
 *
 *      服务器在**美国**，用户在**国内**。手机照片 3~12MB，
 *      跨国上行链路传这么大的东西要十几秒甚至更久，
 *      而且这段时间用户什么反馈都看不到。压到 1600px 之后
 *      通常只剩 300~600KB，一秒左右就传完了。
 *
 * ★ 所以这个模块**任何一步失败都原样退回原文件**，绝不抛异常、
 *   绝不因为"压不了"就不让用户传 —— 后端照样能收（只要不超过
 *   MAX_UPLOAD_BYTES），只是慢一点。宁可慢，不能传不了。
 */

/**
 * 长边的目标像素数。**必须和后端 image_utils.MAX_UPLOAD_EDGE 一致** ——
 * 两边不一样的话，前端压完后端还要再压一次，白掉一层画质。
 * （所以这个数字在后端也有，改的时候两边一起改。）
 */
export const MAX_UPLOAD_EDGE = 1600

/**
 * JPEG 质量。和后端 _UPLOAD_JPEG_QUALITY=88 对齐：照片的细节比插画多，
 * 压过头一眼能看出来（天空会出现色带）。
 * 这里略低一点(0.85)是因为前端压完后端还会经手一次，
 * 把余量留给那一次，避免两次压缩的损失叠加得太明显。
 */
const JPEG_QUALITY = 0.85

/**
 * 小于这个体积就不折腾了。
 *
 * ★ 不是"省点 CPU"，而是**重编码有时候会让图变大**：
 *   已经压过的小图再走一遍 JPEG 编码，掉画质的同时体积还可能增加。
 *   这种情况直接原样上传，两端都省事。
 */
const SKIP_BELOW_BYTES = 400 * 1024

/** 等比缩到长边不超过 maxEdge。已经够小就返回原尺寸（不放大）。 */
function fitInside(
  width: number,
  height: number,
  maxEdge: number,
): { width: number; height: number } {
  const longest = Math.max(width, height)
  if (longest <= maxEdge) {
    return { width, height }
  }
  const scale = maxEdge / longest
  return {
    // Math.max(1, ...) 是防"极端细长的图缩成 0 像素"——0 宽的 canvas 画不出东西
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  }
}

/** 把文件名换成 .jpg（内容和扩展名对不上时浏览器会显示成破图） */
function renameToJpg(name: string): string {
  const base = name.replace(/\.[^./\\]+$/, '') || 'photo'
  return `${base}.jpg`
}

/**
 * 用浏览器解码图片。
 *
 * ★★ imageOrientation: 'from-image' 是**必须显式写**的，不是可选项。
 *
 *    手机竖着拍的照片，文件里存的像素其实是**横的**，
 *    靠 EXIF 里的 Orientation 标记告诉查看器"显示时转 90°"。
 *    而下面 canvas 重编码会**把 EXIF 整个丢掉** ——
 *    于是方向信息只剩"已经画进 canvas 的像素"这一份了。
 *    这里不转，传上去的竖图就是躺倒的，而且是**永久**躺倒：
 *    后端拿到的已经是一张没有方向标记的图，无从补救。
 *    （后端也做了同一件事 —— image_utils 里的 ImageOps.exif_transpose，
 *      两道都要有：这里只覆盖"浏览器压得动"的情况，
 *      老浏览器 / canvas 被禁用时原图会直接传上去，那时靠后端。）
 *
 * ★ 解码不了就返回 null，让调用方退回原文件。
 */
async function decode(file: File): Promise<ImageBitmap | null> {
  if (typeof createImageBitmap !== 'function') {
    // 老浏览器。退回原文件让后端处理 —— 后端有 exif_transpose，
    // 方向照样是对的，只是要多传几 MB。
    return null
  }
  try {
    return await createImageBitmap(file, { imageOrientation: 'from-image' })
  } catch {
    // 这个选项个别旧版本不认；不认就宁可不解码（理由同上）
    return null
  }
}

/**
 * 把一张照片压成适合上传的 JPEG。
 *
 * @returns 压好的 File；**压不了就返回原文件**（永远不抛异常，永远有得传）
 */
export async function shrinkImageForUpload(file: File): Promise<File> {
  // 够小就别动，见 SKIP_BELOW_BYTES 的说明
  if (file.size <= SKIP_BELOW_BYTES) {
    return file
  }

  try {
    const bitmap = await decode(file)
    if (!bitmap) {
      return file
    }

    const { width, height } = fitInside(bitmap.width, bitmap.height, MAX_UPLOAD_EDGE)

    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height

    const context = canvas.getContext('2d')
    if (!context) {
      bitmap.close()
      return file
    }

    // 铺白底：带透明通道的 PNG 直接转 JPEG 会变黑块
    context.fillStyle = '#ffffff'
    context.fillRect(0, 0, width, height)
    context.drawImage(bitmap, 0, 0, width, height)
    // ImageBitmap 占的是**非托管内存**，不 close 要等 GC 才回收。
    // 一次选十张照片就是十张全尺寸位图压在内存里，手机上很容易崩标签页。
    bitmap.close()

    const blob = await new Promise<Blob | null>((resolve) => {
      canvas.toBlob(resolve, 'image/jpeg', JPEG_QUALITY)
    })
    if (!blob) {
      return file
    }

    // 压完反而更大（见 SKIP_BELOW_BYTES 的说明）就别用
    if (blob.size >= file.size) {
      return file
    }

    return new File([blob], renameToJpg(file.name), {
      type: 'image/jpeg',
      lastModified: file.lastModified,
    })
  } catch {
    // ★ 压缩失败**不能**让上传失败。上面的每一步都单独判过了，
    //   这里是兜底：真出了意料之外的情况（比如 canvas 内存不足），
    //   原样上传，让后端去处理。
    return file
  }
}
