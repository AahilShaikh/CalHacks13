"""Orthographic CAD reference blueprints from a metric mesh.

Ported from ``generate_cad_blueprints.py``. Renders Top, Front, Right and
Isometric views with millimeter rulers and dimension callouts so the CAD agent
can read engineering-style dimensions straight off the image. Runs headless
(Agg backend) and never prints; callers surface the returned path.
"""
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402  (backend must be set first)
import numpy as np  # noqa: E402
import trimesh  # noqa: E402


def render_mesh_projections(mesh_path, output_image_path):
    """Render the 2x2 engineering blueprint PNG for ``mesh_path`` and return its path."""
    mesh = trimesh.load(str(mesh_path), force='mesh')
    if mesh.is_empty or len(mesh.vertices) == 0:
        raise ValueError(f'Mesh has no geometry: {mesh_path}')

    bounds = mesh.bounds
    w_x, d_y, h_z = (float(v) for v in mesh.extents)
    vertices = mesh.vertices

    fig, axes = plt.subplots(2, 2, figsize=(14, 12), facecolor='#f7f9fa')
    plt.subplots_adjust(wspace=0.25, hspace=0.3)

    ax_top = axes[0, 0]
    ax_front = axes[0, 1]
    ax_right = axes[1, 0]

    grid_style = {'color': '#d0d7de', 'linestyle': '--', 'linewidth': 0.8}
    mesh_color = '#0969da'
    x_min, x_max = bounds[0][0], bounds[1][0]
    y_min, y_max = bounds[0][1], bounds[1][1]
    z_min, z_max = bounds[0][2], bounds[1][2]

    # 1. Top view (XY plane, looking down from +Z)
    ax_top.set_title(f'TOP VIEW (X-Y Plane)\nWidth: {w_x:.1f} mm | Depth: {d_y:.1f} mm',
                     fontsize=11, fontweight='bold', pad=10)
    ax_top.scatter(vertices[:, 0], vertices[:, 1], s=0.2, c=mesh_color, alpha=0.3)
    ax_top.set_xlabel('X (mm) [Width]', fontsize=10)
    ax_top.set_ylabel('Y (mm) [Depth]', fontsize=10)
    ax_top.grid(True, **grid_style)
    ax_top.set_aspect('equal')
    ax_top.axhline(0, color='gray', linewidth=0.6)
    ax_top.axvline(0, color='gray', linewidth=0.6)

    dim_offset_y = y_min - (d_y * 0.15)
    ax_top.annotate('', xy=(x_max, dim_offset_y), xytext=(x_min, dim_offset_y),
                    arrowprops=dict(arrowstyle='<->', color='#cf222e', lw=1.5))
    ax_top.text((x_min + x_max) / 2.0, dim_offset_y - (d_y * 0.08), f'{w_x:.1f} mm',
                color='#cf222e', fontweight='bold', ha='center', fontsize=9)

    # 2. Front view (XZ plane, looking from -Y)
    ax_front.set_title(f'FRONT VIEW (X-Z Plane)\nWidth: {w_x:.1f} mm | Height: {h_z:.1f} mm',
                       fontsize=11, fontweight='bold', pad=10)
    ax_front.scatter(vertices[:, 0], vertices[:, 2], s=0.2, c=mesh_color, alpha=0.3)
    ax_front.set_xlabel('X (mm) [Width]', fontsize=10)
    ax_front.set_ylabel('Z (mm) [Height from Turntable]', fontsize=10)
    ax_front.grid(True, **grid_style)
    ax_front.set_aspect('equal')
    ax_front.axhline(0, color='#1f883d', linewidth=1.5, label='Turntable (Z=0)')
    ax_front.axvline(0, color='gray', linewidth=0.6)
    ax_front.legend(loc='upper right', fontsize=8)

    dim_offset_x = x_max + (w_x * 0.15)
    ax_front.annotate('', xy=(dim_offset_x, z_max), xytext=(dim_offset_x, z_min),
                      arrowprops=dict(arrowstyle='<->', color='#cf222e', lw=1.5))
    ax_front.text(dim_offset_x + (w_x * 0.08), (z_min + z_max) / 2.0, f'H = {h_z:.1f} mm',
                  color='#cf222e', fontweight='bold', va='center', rotation=-90, fontsize=9)

    # 3. Right view (YZ plane, looking from +X)
    ax_right.set_title(f'RIGHT VIEW (Y-Z Plane)\nDepth: {d_y:.1f} mm | Height: {h_z:.1f} mm',
                       fontsize=11, fontweight='bold', pad=10)
    ax_right.scatter(vertices[:, 1], vertices[:, 2], s=0.2, c=mesh_color, alpha=0.3)
    ax_right.set_xlabel('Y (mm) [Depth]', fontsize=10)
    ax_right.set_ylabel('Z (mm) [Height from Turntable]', fontsize=10)
    ax_right.grid(True, **grid_style)
    ax_right.set_aspect('equal')
    ax_right.axhline(0, color='#1f883d', linewidth=1.5)
    ax_right.axvline(0, color='gray', linewidth=0.6)

    # 4. Isometric 3D view
    axes[1, 1].remove()
    ax_iso_3d = fig.add_subplot(2, 2, 4, projection='3d')
    ax_iso_3d.set_title('ISOMETRIC VIEW (3D Metric)', fontsize=11, fontweight='bold', pad=10)

    step = max(1, len(vertices) // 10000)
    sample_verts = vertices[::step]
    ax_iso_3d.scatter(sample_verts[:, 0], sample_verts[:, 1], sample_verts[:, 2],
                      s=0.3, c=mesh_color, alpha=0.3)
    ax_iso_3d.set_xlabel('X (mm)', fontsize=9)
    ax_iso_3d.set_ylabel('Y (mm)', fontsize=9)
    ax_iso_3d.set_zlabel('Z (mm)', fontsize=9)
    ax_iso_3d.view_init(elev=25, azim=45)

    max_range = max(w_x, d_y, h_z) / 2.0
    mid_x = (x_min + x_max) * 0.5
    mid_y = (y_min + y_max) * 0.5
    ax_iso_3d.set_xlim(mid_x - max_range, mid_x + max_range)
    ax_iso_3d.set_ylim(mid_y - max_range, mid_y + max_range)
    ax_iso_3d.set_zlim(0, max_range * 2.0)

    plt.suptitle(
        f'PARAMETRIC CAD REFERENCE BLUEPRINT (1:1 Metric Scale)\n'
        f'Bounding Box: {w_x:.1f} mm (W) x {d_y:.1f} mm (D) x {h_z:.1f} mm (H)',
        fontsize=13, fontweight='bold', y=0.98)

    plt.savefig(str(output_image_path), dpi=200, bbox_inches='tight')
    plt.close()
    return str(output_image_path)
