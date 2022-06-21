from common.transformations.camera import normalize, get_view_frame_from_calib_frame
from common.transformations.model import medmodel_intrinsics
import common.transformations.orientation as orient
import numpy as np
import math
import os
import cv2

PATH_TO_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
FULL_FRAME_SIZE = (1164, 874)  # input frame to openpilot model
W, H = FULL_FRAME_SIZE[0], FULL_FRAME_SIZE[1]
eon_focal_length = FOCAL = 910.0  # focal length of eon devkit camera (LeECO Pro 3 smartphone camera)

# aka 'K' aka camera_frame_from_view_frame
eon_intrinsics = np.array(
    [  # intrinsics camera array. For more info: https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html
        [FOCAL, 0., W / 2.],
        [0., FOCAL, H / 2.],
        [0., 0., 1.]])

ground_from_medmodel_frame = [
    [0.00000000e+00, 0.00000000e+00, 1.00000000e+00],
    [-1.09890110e-03, 0.00000000e+00, 2.81318681e-01],
    [-1.84808520e-20, 9.00738606e-04, -4.28751576e-02]]

# no clue what this stuff is :( probably something to do with scaling
X_IDXs = [
    0., 0.1875, 0.75, 1.6875, 3., 4.6875,
    6.75, 9.1875, 12., 15.1875, 18.75, 22.6875,
    27., 31.6875, 36.75, 42.1875, 48., 54.1875,
    60.75, 67.6875, 75., 82.6875, 90.75, 99.1875,
    108., 117.1875, 126.75, 136.6875, 147., 157.6875,
    168.75, 180.1875, 192.]

_BB_SCALE = W / 640.

_BB_TO_FULL_FRAME = np.asarray([
    [_BB_SCALE, 0., 0.],
    [0., _BB_SCALE, 0.],
    [0., 0., 1.]])


def printf(*args, **kwargs):
    print(flush=True, *args, **kwargs)


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def extract_preds(outputs, best_plan_only=True):
    """
    This function extracts lane lines, road edges and estimated plan outputs from the model output tensor.
    :param outputs: Output tensor from the openpilot supercombo model
    :param best_plan_only: Default True, will only output driving plan with highest confidence otherwise will output
           all plans
    :return: Tuple ((lanelines, lanelines_probs), (road_edges, road_edges_probs), plan).
    """
    # N is batch_size

    plan_start_idx = 0
    plan_end_idx = 4955

    lanes_start_idx = plan_end_idx
    lanes_end_idx = lanes_start_idx + 528

    lane_lines_prob_start_idx = lanes_end_idx
    lane_lines_prob_end_idx = lane_lines_prob_start_idx + 8

    road_start_idx = lane_lines_prob_end_idx
    road_end_idx = road_start_idx + 264

    # plan
    plan = outputs[:, plan_start_idx:plan_end_idx]  # (N, 4955)
    plans = plan.reshape((-1, 5, 991))  # (N, 5, 991)
    plan_probs = plans[:, :, -1]  # (N, 5)
    plans = plans[:, :, :-1].reshape(-1, 5, 2, 33, 15)  # (N, 5, 2, 33, 15)
    best_plan_idx = np.argmax(plan_probs, axis=1)[0]  # (N,)
    best_plan = plans[:, best_plan_idx, ...]  # (N, 2, 33, 15)

    # lane lines
    lane_lines = outputs[:, lanes_start_idx:lanes_end_idx]  # (N, 528)
    lane_lines_deflat = lane_lines.reshape((-1, 2, 264))  # (N, 2, 264)
    lane_lines_means = lane_lines_deflat[:, 0, :]  # (N, 264)
    lane_lines_means = lane_lines_means.reshape(-1, 4, 33, 2)  # (N, 4, 33, 2)

    outer_left_lane = lane_lines_means[:, 0, :, :]  # (N, 33, 2)
    inner_left_lane = lane_lines_means[:, 1, :, :]  # (N, 33, 2)
    inner_right_lane = lane_lines_means[:, 2, :, :]  # (N, 33, 2)
    outer_right_lane = lane_lines_means[:, 3, :, :]  # (N, 33, 2)

    # lane lines probs
    lane_lines_probs = outputs[:, lane_lines_prob_start_idx:lane_lines_prob_end_idx]  # (N, 8)
    lane_lines_probs = lane_lines_probs.reshape((-1, 4, 2))  # (N, 4, 2)
    lane_lines_probs = sigmoid(lane_lines_probs[:, :, 1])  # (N, 4), 0th is deprecated

    outer_left_prob = lane_lines_probs[:, 0]  # (N,)
    inner_left_prob = lane_lines_probs[:, 1]  # (N,)
    inner_right_prob = lane_lines_probs[:, 2]  # (N,)
    outer_right_prob = lane_lines_probs[:, 3]  # (N,)

    # road edges
    road_edges = outputs[:, road_start_idx:road_end_idx]
    road_edges_deflat = road_edges.reshape((-1, 2, 132))  # (N, 2, 132)
    road_edge_means = road_edges_deflat[:, 0, :].reshape(-1, 2, 33, 2)  # (N, 2, 33, 2)
    road_edge_stds = road_edges_deflat[:, 1, :].reshape(-1, 2, 33, 2)  # (N, 2, 33, 2)

    left_edge = road_edge_means[:, 0, :, :]  # (N, 33, 2)
    right_edge = road_edge_means[:, 1, :, :]
    left_edge_std = road_edge_stds[:, 0, :, :]  # (N, 33, 2)
    right_edge_std = road_edge_stds[:, 1, :, :]

    batch_size = best_plan.shape[0]

    result_batch = []

    for i in range(batch_size):
        lanelines = [outer_left_lane[i], inner_left_lane[i], inner_right_lane[i], outer_right_lane[i]]
        lanelines_probs = [outer_left_prob[i], inner_left_prob[i], inner_right_prob[i], outer_right_prob[i]]
        road_edges = [left_edge[i], right_edge[i]]
        road_edges_probs = [left_edge_std[i], right_edge_std[i]]

        if best_plan_only:
            plan = best_plan[i]
        else:
            plan = (plans[i], plan_probs[i])

        result_batch.append(((lanelines, lanelines_probs), (road_edges, road_edges_probs), plan))

    return result_batch



def get_transform_matrix(base_img,
                         augment_trans=np.array([0, 0, 0]),
                         augment_eulers=np.array([0, 0, 0]),
                         from_intr=eon_intrinsics,
                         to_intr=eon_intrinsics,
                         output_size=None,
                         h=1.22):
    size = base_img.shape[:2]
    if not output_size:
        output_size = size[::-1]

    cy = from_intr[1, 2]

    quadrangle = np.array([[0, cy + 20],
                           [size[1] - 1, cy + 20],
                           [0, size[0] - 1],
                           [size[1] - 1, size[0] - 1]], dtype=np.float32)
    quadrangle_norm = np.hstack((normalize(quadrangle, intrinsics=from_intr), np.ones((4, 1))))
    quadrangle_world = np.column_stack((h * quadrangle_norm[:, 0] / quadrangle_norm[:, 1],
                                        h * np.ones(4),
                                        h / quadrangle_norm[:, 1]))
    rot = orient.rot_from_euler(augment_eulers)
    to_extrinsics = np.hstack((rot.T, -augment_trans[:, None]))
    to_KE = to_intr.dot(to_extrinsics)
    warped_quadrangle_full = np.einsum('jk,ik->ij', to_KE, np.hstack((quadrangle_world, np.ones((4, 1)))))
    warped_quadrangle = np.column_stack((warped_quadrangle_full[:, 0] / warped_quadrangle_full[:, 2],
                                         warped_quadrangle_full[:, 1] / warped_quadrangle_full[:, 2])).astype(
        np.float32)

    M = cv2.getPerspectiveTransform(quadrangle, warped_quadrangle.astype(np.float32))

    return M


def transform_img(base_img,
                  augment_trans=np.array([0, 0, 0]),
                  augment_eulers=np.array([0, 0, 0]),
                  from_intr=eon_intrinsics,
                  to_intr=eon_intrinsics,
                  output_size=None,
                  pretransform=None,
                  top_hacks=False,
                  yuv=False,
                  alpha=1.0,
                  beta=0,
                  blur=0):
    # import cv2  # pylint: disable=import-error
    cv2.setNumThreads(1)

    if yuv:
        base_img = cv2.cvtColor(base_img, cv2.COLOR_YUV2RGB_I420)

    size = base_img.shape[:2]
    if not output_size:
        output_size = size[::-1]

    cy = from_intr[1, 2]

    def get_M(h=1.22):
        quadrangle = np.array([[0, cy + 20],
                               [size[1] - 1, cy + 20],
                               [0, size[0] - 1],
                               [size[1] - 1, size[0] - 1]], dtype=np.float32)
        quadrangle_norm = np.hstack((normalize(quadrangle, intrinsics=from_intr), np.ones((4, 1))))
        quadrangle_world = np.column_stack((h * quadrangle_norm[:, 0] / quadrangle_norm[:, 1],
                                            h * np.ones(4),
                                            h / quadrangle_norm[:, 1]))
        rot = orient.rot_from_euler(augment_eulers)
        to_extrinsics = np.hstack((rot.T, -augment_trans[:, None]))
        to_KE = to_intr.dot(to_extrinsics)
        warped_quadrangle_full = np.einsum('jk,ik->ij', to_KE, np.hstack((quadrangle_world, np.ones((4, 1)))))
        warped_quadrangle = np.column_stack((warped_quadrangle_full[:, 0] / warped_quadrangle_full[:, 2],
                                             warped_quadrangle_full[:, 1] / warped_quadrangle_full[:, 2])).astype(
            np.float32)
        M = cv2.getPerspectiveTransform(quadrangle, warped_quadrangle.astype(np.float32))
        return M

    M = get_M()
    if pretransform is not None:
        M = M.dot(pretransform)
    augmented_rgb = cv2.warpPerspective(base_img, M, output_size, borderMode=cv2.BORDER_REPLICATE)

    if top_hacks:
        cyy = int(math.ceil(to_intr[1, 2]))
        M = get_M(1000)
        if pretransform is not None:
            M = M.dot(pretransform)
        augmented_rgb[:cyy] = cv2.warpPerspective(base_img, M, (output_size[0], cyy), borderMode=cv2.BORDER_REPLICATE)

    # brightness and contrast augment
    # augmented_rgb = np.clip((float(alpha)*augmented_rgb + beta), 0, 255).astype(np.uint8)

    # print('after clip:', augmented_rgb.shape, augmented_rgb.dtype)
    # gaussian blur
    if blur > 0:
        augmented_rgb = cv2.GaussianBlur(augmented_rgb, (blur * 2 + 1, blur * 2 + 1), cv2.BORDER_DEFAULT)

    if yuv:
        augmented_img = cv2.cvtColor(augmented_rgb, cv2.COLOR_RGB2YUV_I420)
    else:
        augmented_img = augmented_rgb

    return augmented_img


def reshape_yuv(frames):
    H = (frames.shape[1] * 2) // 3
    W = frames.shape[2]
    in_img1 = np.zeros((frames.shape[0], 6, H // 2, W // 2), dtype=np.uint8)

    in_img1[:, 0] = frames[:, 0:H:2, 0::2]
    in_img1[:, 1] = frames[:, 1:H:2, 0::2]
    in_img1[:, 2] = frames[:, 0:H:2, 1::2]
    in_img1[:, 3] = frames[:, 1:H:2, 1::2]
    in_img1[:, 4] = frames[:, H:H + H // 4].reshape((-1, H // 2, W // 2))
    in_img1[:, 5] = frames[:, H + H // 4:H + H // 2].reshape((-1, H // 2, W // 2))
    return in_img1


# color space conversion methods
def bgr_to_yuv(img_bgr):
    """
    Model accepts images in the YUV color space. This function converts a BGR (blue, green, red) to YUV420
    See: https://github.com/commaai/openpilot/tree/master/selfdrive/modeld/models
    :param img_bgr: BGR Image (numpy array)
    :return: image in YUV420 color space
    """
    img_yuv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2YUV_I420)
    assert img_yuv.shape == ((874 * 3 // 2, 1164))
    return img_yuv


def bgr_to_rgb(bgr):
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def yuv_to_rgb(yuv):
    return cv2.cvtColor(yuv, cv2.COLOR_YUV2RGB_I420)


def rgb_to_yuv(rgb):
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2YUV_I420)


def transform_frames(frames):
    """
    This function transforms input such that they are acceptable by the model.
    See: https://github.com/commaai/openpilot/tree/master/selfdrive/modeld/models
    :param frames: A list with two consecutive frames captured at 20 Hz.
    :return: Transformed images to feed into openpilot model input frame tensor.
    """
    imgs_med_model = np.zeros((len(frames), 384, 512), dtype=np.uint8)
    for i, img in enumerate(frames):
        imgs_med_model[i] = transform_img(img,
                                          from_intr=eon_intrinsics,
                                          to_intr=medmodel_intrinsics,
                                          yuv=True,
                                          output_size=(512, 256))

    reshaped = reshape_yuv(imgs_med_model)

    return reshaped


class Calibration:
    """ The Calibration class provides functionality to map points between model space (meters) to image space and vice
        versa (pixels).
        TODO: Need to figure out live calibration.
    """

    def __init__(self, rpy, intrinsic=eon_intrinsics, plot_img_width=640, plot_img_height=480):
        self.intrinsic = intrinsic
        self.extrinsics_matrix = get_view_frame_from_calib_frame(rpy[0], rpy[1], rpy[2], 1.22)[:, :3]
        self.plot_img_width = plot_img_width
        self.plot_img_height = plot_img_height
        self.zoom = W / plot_img_width
        self.CALIB_BB_TO_FULL = np.asarray([
            [self.zoom, 0., 0.],
            [0., self.zoom, 0.],
            [0., 0., 1.]])

    def car_space_to_ff(self, x, y, z):
        car_space_projective = np.column_stack((x, y, z)).T
        ep = self.extrinsics_matrix.dot(car_space_projective)
        kep = self.intrinsic.dot(ep)
        # TODO: fix numerical instability (add 1e-16)
        # UPD: this turned out to slow things down a lot. How do we do it then?
        return (kep[:-1, :] / kep[-1, :]).T

    def car_space_to_bb(self, x, y, z):
        pts = self.car_space_to_ff(x, y, z)
        return pts / self.zoom


def project_path(path, calibration, z_off):
    '''Projects paths from calibration space (model input/output) to image space.'''

    x = path[:, 0]
    y = path[:, 1]
    z = path[:, 2] + z_off
    pts = calibration.car_space_to_bb(x, y, z)
    # filter out invalid points
    pts[pts < 0] = np.nan
    valid = np.isfinite(pts).all(axis=1)
    pts = pts[valid].astype(int)

    return pts


def create_image_canvas(img_rgb, zoom_matrix, plot_img_height, plot_img_width):
    '''Transform with a correct warp/zoom transformation.'''
    img_plot = np.zeros((plot_img_height, plot_img_width, 3), dtype='uint8')
    cv2.warpAffine(img_rgb, zoom_matrix[:2], (img_plot.shape[1], img_plot.shape[0]), dst=img_plot,
                   flags=cv2.WARP_INVERSE_MAP)
    return img_plot


def draw_path(lane_lines, road_edges, path_plan, img_plot, calibration, lane_line_color_list, width=1, height=1.22,
              fill_color=(128, 0, 255), line_color=(0, 255, 0)):
    '''Draw model predictions on an image.'''

    overlay = img_plot.copy()
    alpha = 0.4
    fixed_distances = np.array(X_IDXs)[:, np.newaxis]

    # lane_lines are sequentially parsed ::--> means--> std's
    if lane_lines is not None:
        (oll, ill, irl, orl), (oll_prob, ill_prob, irl_prob, orl_prob) = lane_lines

        calib_pts_oll = np.hstack((fixed_distances, oll))  # (33, 3)
        calib_pts_ill = np.hstack((fixed_distances, ill))  # (33, 3)
        calib_pts_irl = np.hstack((fixed_distances, irl))  # (33, 3)
        calib_pts_orl = np.hstack((fixed_distances, orl))  # (33, 3)

        img_pts_oll = project_path(calib_pts_oll, calibration, z_off=0).reshape(-1, 1, 2)
        img_pts_ill = project_path(calib_pts_ill, calibration, z_off=0).reshape(-1, 1, 2)
        img_pts_irl = project_path(calib_pts_irl, calibration, z_off=0).reshape(-1, 1, 2)
        img_pts_orl = project_path(calib_pts_orl, calibration, z_off=0).reshape(-1, 1, 2)

        lane_lines_with_probs = [(img_pts_oll, oll_prob), (img_pts_ill, ill_prob), (img_pts_irl, irl_prob),
                                 (img_pts_orl, orl_prob)]

        line_overlay = overlay.copy()
        # plot lanelines
        for i, (line_pts, prob) in enumerate(lane_lines_with_probs):
            cv2.polylines(line_overlay, [line_pts], False, lane_line_color_list[i], thickness=2)
            img_plot = line_overlay

    # road edges
    if road_edges is not None:
        (left_road_edge, right_road_edge), _ = road_edges

        calib_pts_ledg = np.hstack((fixed_distances, left_road_edge))
        calib_pts_redg = np.hstack((fixed_distances, right_road_edge))

        img_pts_ledg = project_path(calib_pts_ledg, calibration, z_off=0).reshape(-1, 1, 2)
        img_pts_redg = project_path(calib_pts_redg, calibration, z_off=0).reshape(-1, 1, 2)

        # plot road_edges
        cv2.polylines(overlay, [img_pts_ledg], False, (255, 128, 0), thickness=2)
        cv2.polylines(overlay, [img_pts_redg], False, (255, 234, 0), thickness=2)

    # path plan
    if path_plan is not None:

        path_plan_l = path_plan - np.array([0, 1, 0])
        path_plan_r = path_plan + np.array([0, 1, 0])

        img_pts_l = project_path(path_plan_l, calibration, z_off=height)
        img_pts_r = project_path(path_plan_r, calibration, z_off=height)

        for i in range(1, len(img_pts_l)):
            if i >= len(img_pts_r): break

            u1, v1, u2, v2 = np.append(img_pts_l[i - 1], img_pts_r[i - 1])
            u3, v3, u4, v4 = np.append(img_pts_l[i], img_pts_r[i])
            pts = np.array([[u1, v1], [u2, v2], [u4, v4], [u3, v3]], np.int32).reshape((-1, 1, 2))
            cv2.fillPoly(overlay, [pts], fill_color)
            cv2.polylines(overlay, [pts], True, line_color)

    # drawing the plots on original iamge
    img_plot = cv2.addWeighted(overlay, alpha, img_plot, 1 - alpha, 0)

    return img_plot
