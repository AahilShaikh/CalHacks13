"""Aida's standalone ComfyUI nodes. Install this folder as custom_nodes/aida_scan.

ComfyUI-only imports are lazy so the photo locator can also run in Aida's API.
"""
import os

NONE_IMAGE = '(none)'
VIEWS = ('front', 'left', 'back', 'right')


class AidaOptionalImage:
    @classmethod
    def INPUT_TYPES(cls):
        import folder_paths
        files = folder_paths.filter_files_content_types(
            sorted(os.listdir(folder_paths.get_input_directory())), ['image'])
        return {'required': {'image': ([NONE_IMAGE] + files, {'image_upload': True})}}

    RETURN_TYPES = ('IMAGE',)
    RETURN_NAMES = ('optional_image',)
    FUNCTION = 'load'
    CATEGORY = 'Aida/Scan'

    def load(self, image):
        if image == NONE_IMAGE:
            return (None,)
        from nodes import LoadImage
        return (LoadImage().load_image(image)[0],)

    @classmethod
    def VALIDATE_INPUTS(cls, image):
        if image == NONE_IMAGE:
            return True
        from nodes import LoadImage
        return LoadImage.VALIDATE_INPUTS(image)

    @classmethod
    def IS_CHANGED(cls, image):
        if image == NONE_IMAGE:
            return NONE_IMAGE
        from nodes import LoadImage
        return LoadImage.IS_CHANGED(image)


class AidaObjectOnlyViews:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'model': ('MODEL',), 'front': ('IMAGE',),
                             'padding': ('FLOAT', {'default': 1.1, 'min': 1., 'max': 2., 'step': .01})},
                'optional': {**{v: ('IMAGE',) for v in VIEWS[1:]},
                             'background_model': ('BACKGROUND_REMOVAL',),
                             'camera_focal_px': ('FLOAT', {'default':0., 'forceInput':True})}}

    RETURN_TYPES = ('IMAGE',)*5 + ('MASK',)*4 + ('STRING','FLOAT')
    RETURN_NAMES = ('front_object', 'left_object', 'back_object', 'right_object',
                    'object_previews', 'front_mask', 'left_mask', 'back_mask', 'right_mask', 'evidence', 'fov_degrees')
    FUNCTION = 'isolate'
    CATEGORY = 'Aida/Scan'
    DESCRIPTION = ('Automatically locate the object on the ChArUco print and segment it with SAM 3.1. '
                   'Only black-background object images leave this node. Unused side views may be (none).')

    def isolate(self, model, front, padding=1.1, left=None, back=None, right=None, background_model=None, camera_focal_px=0.):
        import json
        import math
        import numpy as np
        import torch
        from PIL import Image
        from comfy_extras.nodes_sam3 import SAM3_Detect
        from .board import board_object_region

        if front is None:
            raise ValueError('A front photo is required.')
        photos = dict(zip(VIEWS, (front, left, back, right)))
        masks, boxes, regions, originals = {}, {}, {}, {}
        for name, photo in photos.items():
            if photo is None:
                continue
            if photo.shape[0] != 1:
                raise ValueError('Load one photo per cardinal view; image batches are unsupported.')
            rgb = np.rint(photo[0, :, :, :3].detach().cpu().numpy().clip(0, 1)*255).astype(np.uint8)
            region = board_object_region(rgb[:, :, ::-1].copy())
            if region is None:
                if background_model is None:
                    raise ValueError(f'{name}: no ChArUco board found and no background model supplied.')
                from comfy_extras.nodes_bg_removal import RemoveBackground
                mask_tensor = RemoveBackground.execute(background_model, photo)[0].detach().cpu()
            else:
                mask_tensor = SAM3_Detect.execute(model, photo, bboxes=region,
                    threshold=.5, refine_iterations=1, individual_masks=False)[0].detach().cpu()
            binary = (mask_tensor[0].numpy() > .5).astype(np.uint8)*255
            mask = Image.fromarray(binary)
            box = mask.getbbox()
            if box is None:
                raise ValueError(f'{name}: SAM found no object; improve lighting or framing.')
            prompt = region or {'x':0,'y':0,'width':rgb.shape[1],'height':rgb.shape[0]}
            x, y, w, h = (prompt[k] for k in ('x','y','width','height'))
            inside = np.count_nonzero(binary[y:y+h, x:x+w])
            area = np.count_nonzero(binary)
            if region is not None and (area > .5*binary.size or inside < .9*area):
                raise ValueError(f'{name}: segmentation includes the board; refusing to generate a turntable mesh.')
            masks[name], boxes[name], regions[name] = mask, box, region
            originals[name] = Image.fromarray(rgb)
        if not masks:
            raise ValueError('A front photo is required.')
        side = max(1, round(max(max(b[2]-b[0], b[3]-b[1]) for b in boxes.values())*padding))
        output = {}
        for name, mask in masks.items():
            photo = originals[name]
            composite = Image.composite(photo, Image.new('RGB', photo.size), mask)
            x0,y0,x1,y1 = boxes[name]
            x, y = round((x0+x1-side)/2), round((y0+y1-side)/2)
            framed = composite.crop((x,y,x+side,y+side)).resize((1024,1024),Image.Resampling.LANCZOS)
            output[name] = torch.from_numpy(np.asarray(framed).copy().astype(np.float32)/255.)[None]
        preview = torch.cat([output[v] for v in VIEWS if v in output])
        raw_masks = {name: torch.from_numpy(np.asarray(mask).copy().astype(np.float32)/255.)[None]
                     for name,mask in masks.items()}
        fov = math.degrees(2*math.atan(side/(2*camera_focal_px))) if camera_focal_px>0 else 20.
        evidence = json.dumps({'fov_degrees':fov,'views': list(output), 'object_boxes': boxes,
            'automatic_prompts': regions, 'common_crop_pixels': side,
            'segmentation': 'ChArUco-guided SAM 3.1; board excluded'})
        return (*[output.get(v) for v in VIEWS], preview,
                *[raw_masks.get(v) for v in VIEWS], evidence, fov)


NODE_CLASS_MAPPINGS = {'AidaOptionalImage': AidaOptionalImage, 'AidaObjectOnlyViews': AidaObjectOnlyViews}
NODE_DISPLAY_NAME_MAPPINGS = {'AidaOptionalImage': 'Aida: Optional View (none = unused)',
                             'AidaObjectOnlyViews': 'Aida: Object Only — Remove ChArUco Turntable'}
