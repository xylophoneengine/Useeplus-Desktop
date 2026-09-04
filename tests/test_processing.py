import numpy as np
from microscope.processing import Params, apply, DEFAULT_ROTATION


def frame():
    rng = np.random.default_rng(0)
    return rng.integers(0, 256, (480, 640, 3), dtype=np.uint8)


def test_default_rotation_is_90():
    assert DEFAULT_ROTATION == 90
    assert Params().rotation == 90


def test_identity_returns_equal_copy():
    f = frame()
    p = Params(rotation=0)
    assert p.is_identity()
    out = apply(f, p)
    assert out is not f and np.array_equal(out, f)


def test_rotation_shapes():
    f = frame()
    assert apply(f, Params(rotation=90)).shape == (640, 480, 3)
    assert apply(f, Params(rotation=180)).shape == (480, 640, 3)
    assert apply(f, Params(rotation=270)).shape == (640, 480, 3)


def test_rotation_90_direction_matches_numpy_rot90_clockwise():
    f = frame()
    assert np.array_equal(apply(f, Params(rotation=90)), np.rot90(f, k=-1))


def test_flips():
    f = frame()
    assert np.array_equal(apply(f, Params(rotation=0, flip_h=True)), f[:, ::-1])
    assert np.array_equal(apply(f, Params(rotation=0, flip_v=True)), f[::-1])


def test_brightness_contrast_gamma_monotonic():
    ramp = np.tile(np.arange(256, dtype=np.uint8), (8, 1))[..., None].repeat(3, axis=2)
    for p in (Params(rotation=0, brightness=40), Params(rotation=0, contrast=1.8),
              Params(rotation=0, gamma=0.5), Params(rotation=0, gamma=2.2)):
        out = apply(ramp, p)[0, :, 0].astype(int)
        assert np.all(np.diff(out) >= 0), p
    assert apply(ramp, Params(rotation=0, brightness=40))[0, 100, 0] == 140
    assert apply(ramp, Params(rotation=0, brightness=-40))[0, 20, 0] == 0


def test_sharpen_keeps_dtype_and_shape():
    f = frame()
    out = apply(f, Params(rotation=0, sharpen=1.0))
    assert out.dtype == np.uint8 and out.shape == f.shape
