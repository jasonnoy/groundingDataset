# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved.
import torch
from maskrcnn_benchmark.structures.image_list import to_image_list
from GLIP.maskrcnn_benchmark.data.datasets.laion import *

import pdb


class BatchCollator(object):
    """
    From a list of samples from the dataset,
    returns the batched images and targets.
    This should be passed to the DataLoader
    """

    def __init__(self, size_divisible=0):
        self.size_divisible = size_divisible

    def __call__(self, batch):
        transposed_batch = list(zip(*batch))

        images = to_image_list(transposed_batch[0], self.size_divisible)
        targets = transposed_batch[1]
        img_ids = transposed_batch[2]
        positive_map = None
        positive_map_eval = None
        greenlight_map = None

        if isinstance(targets[0], dict):
            return images, targets, img_ids, positive_map, positive_map_eval

        if "greenlight_map" in transposed_batch[1][0].fields():
            greenlight_map = torch.stack([i.get_field("greenlight_map") for i in transposed_batch[1]], dim=0)

        if "positive_map" in transposed_batch[1][0].fields():
            # we batch the positive maps here
            # Since in general each batch element will have a different number of boxes,
            # we collapse a single batch dimension to avoid padding. This is sufficient for our purposes.
            max_len = max([v.get_field("positive_map").shape[1] for v in transposed_batch[1]])
            nb_boxes = sum([v.get_field("positive_map").shape[0] for v in transposed_batch[1]])
            batched_pos_map = torch.zeros((nb_boxes, max_len), dtype=torch.bool)
            cur_count = 0
            for v in transposed_batch[1]:
                cur_pos = v.get_field("positive_map")
                batched_pos_map[cur_count: cur_count + len(cur_pos), : cur_pos.shape[1]] = cur_pos
                cur_count += len(cur_pos)

            assert cur_count == len(batched_pos_map)
            positive_map = batched_pos_map.float()

        if "positive_map_eval" in transposed_batch[1][0].fields():
            # we batch the positive maps here
            # Since in general each batch element will have a different number of boxes,
            # we collapse a single batch dimension to avoid padding. This is sufficient for our purposes.
            max_len = max([v.get_field("positive_map_eval").shape[1] for v in transposed_batch[1]])
            nb_boxes = sum([v.get_field("positive_map_eval").shape[0] for v in transposed_batch[1]])
            batched_pos_map = torch.zeros((nb_boxes, max_len), dtype=torch.bool)
            cur_count = 0
            for v in transposed_batch[1]:
                cur_pos = v.get_field("positive_map_eval")
                batched_pos_map[cur_count: cur_count + len(cur_pos), : cur_pos.shape[1]] = cur_pos
                cur_count += len(cur_pos)

            assert cur_count == len(batched_pos_map)
            # assert batched_pos_map.sum().item() == sum([v["positive_map"].sum().item() for v in batch[1]])
            positive_map_eval = batched_pos_map.float()

        return images, targets, img_ids, positive_map, positive_map_eval, greenlight_map


def compute_offset_map(str1, str0):
    res = []
    j = 0
    offset = 0
    try:
        assert len(str0) >= len(str1)
    except Exception as e:
        raise AssertionError(f"str1:{str1}, str0:{str0}")
    for i in range(len(str0)):
        if j < len(str1) and str0[i] == str1[j]:
            j += 1
        else:
            offset += 1
        res.append(offset)
    return res


class BatchGroundingCollator(object):
    """
    From a list of samples from the dataset,
    returns the batched images and targets.
    This should be passed to the DataLoader
    """

    def __init__(self, nlp, tokenizer, transforms=None, size_divisible=0, use_json=False):
        self.tokenizer = tokenizer
        self.transform = transforms
        self.nlp = nlp
        self.size_divisible = size_divisible
        self.use_json = use_json

    def process_iamge(self, image):
        image_shape = image.size
        # image_resize_shape = compute_image_shape(image_shape)
        image_resize_shape = (522, 522)
        image = image.resize(image_resize_shape)
        image = np.array(image)[:, :, [2, 1, 0]]
        if self.transform is not None:
            image = self.transform(image)
        return image

    def process_caption(self, origin_caption):

        def is_valid_chunk(chunk: str):
            exclude = ['what', 'where', 'it', 'there', 'these', 'those', 'who', "it's", "its",
                       'whose', 'they', 'their', 'them', 'when', 'how']
            for ex in exclude:
                for word in chunk.lower().split():
                    if ex == word:
                        return False
            return True

        caption = remove_punctuation(origin_caption)
        doc = self.nlp(caption)
        nouns = [t.text for t in doc.noun_chunks if is_valid_chunk(t.text)]
        tokens_positive = [[(int(t[0].idx), int(t[0].idx) + len(t.text))] for t in doc.noun_chunks if
                           is_valid_chunk(t.text)]
        empty_nouns = False
        if len(nouns) == 0:
            print("No entities found, using caption as entity, caption: {}".format(caption))
            nouns = [caption]
            empty_nouns = True
        entity_dict = {}
        new_entities = []
        # to handle duplicates in entities
        for chunk in nouns:
            if chunk not in entity_dict:
                new_entities.append(chunk)
                entity_dict[chunk] = 0
            else:
                entity_dict[chunk] += 1
                new_entities.append("{}-{}".format(chunk, entity_dict[chunk]))
        new_to_old_entity = dict(zip(new_entities, nouns))
        if not empty_nouns:
            # match new entity to its position in the original joint caption
            new_entity_to_id = dict(zip(new_entities,
                                        [(noun_chunk[0].idx, noun_chunk[-1].idx + len(noun_chunk[-1].text)) for
                                         noun_chunk in doc.noun_chunks if is_valid_chunk(
                                            noun_chunk.text)]))  # starting and ending position of the noun chunk
        else:
            # use caption as only entity
            new_entity_to_id = {new_entities[0]: (0, len(caption))}

        # print("new_entity_to_id:", new_entity_to_id)
        tokenized = self.tokenizer([caption], return_tensors="pt")

        # process positive map
        positive_map = create_positive_map(tokenized, tokens_positive)
        return positive_map, new_entities, new_to_old_entity, new_entity_to_id, caption

    def process_caption_with_offset(self, origin_caption):
        caption = remove_punctuation(origin_caption)
        offset_map = compute_offset_map(caption, origin_caption)
        # print("caption:", caption)
        # print("offset_map:", offset_map)
        doc = self.nlp(caption)
        nouns = [t.text for t in doc.noun_chunks]
        # print("nouns: {}".format(nouns))
        # tokens_positive = [[(int(t[0].idx) + offset_map[t[0].idx], int(t[0].idx) + offset_map[int(t[0].idx)] + len(t.text))] for t in doc.noun_chunks]
        # for t in doc.noun_chunks:
        #     print("noun chunk s, e:", t[0].idx, int(t[0].idx) + len(t.text))
        tokens_positive = [[(int(t[0].idx), int(t[0].idx) + len(t.text))] for t in doc.noun_chunks]
        # print("tokens_positive:", tokens_positive)
        empty_nouns = False
        if len(nouns) == 0:
            print("No entities found, using caption as entity, caption: {}".format(caption))
            nouns = [caption]
            empty_nouns = True
        entity_dict = {}
        new_entities = []
        # to handle duplicates in entities
        for chunk in nouns:
            if chunk not in entity_dict:
                new_entities.append(chunk)
                entity_dict[chunk] = 0
            else:
                entity_dict[chunk] += 1
                new_entities.append("{}-{}".format(chunk, entity_dict[chunk]))
        new_to_old_entity = dict(zip(new_entities, nouns))
        if not empty_nouns:
            # match new entity to its position in the original joint caption
            new_entity_to_id = dict(zip(new_entities, [(noun_chunk[0].idx + offset_map[noun_chunk[0].idx],
                                                        noun_chunk[-1].idx + len(noun_chunk[-1].text) + offset_map[
                                                            noun_chunk[-1].idx]) for noun_chunk in
                                                       doc.noun_chunks]))  # starting and ending position of the noun chunk
        else:
            # use caption as only entity
            new_entity_to_id = {new_entities[0]: (offset_map[0], len(caption))}

        # print("new_entity_to_id:", new_entity_to_id)
        tokenized = self.tokenizer([caption], return_tensors="pt")

        # process positive map
        positive_map = create_positive_map(tokenized, tokens_positive)
        return positive_map, new_entities, new_to_old_entity, new_entity_to_id, caption

    def __call__(self, batch):
        images = []
        captions = []
        ids = []
        if self.use_json:
            for d in batch:
                images.append(Image.open(d['image_path']).convert('RGB'))
                captions.append(d['caption'])
                ids.append(d['id'])
        else:
            for d in batch:
                images.append(pil_loader(d['jpg']))
                captions.append(d['txt'].decode())
                ids.append(d['id'].decode())

        origin_images = [np.array(image)[:, :, [2, 1, 0]] for image in images]

        images = [self.process_iamge(img) for img in images]

        positive_maps = []
        entities = []
        new_to_old_entity_list = []
        new_entity_to_id_list = []
        new_caps = []

        for cap in captions:
            positive_map, new_entities, new_to_old_entity, new_entity_to_id, new_caption = self.process_caption(cap)
            positive_maps.append(positive_map)
            entities.append(new_entities)
            new_to_old_entity_list.append(new_to_old_entity)
            new_entity_to_id_list.append(new_entity_to_id)

            new_caps.append(new_caption)

        # compute batched positive map
        max_len = max([v.shape[1] for v in positive_maps])
        nb_boxes = sum([v.shape[0] for v in positive_maps])
        batched_pos_map = torch.zeros((nb_boxes, max_len), dtype=torch.bool)
        cur_count = 0
        for cur_pos in positive_maps:
            batched_pos_map[cur_count: cur_count + len(cur_pos), : cur_pos.shape[1]] = cur_pos
            cur_count += len(cur_pos)

        max_size = tuple(max(s) for s in zip(*[img.shape for img in images]))
        if self.size_divisible > 0:
            import math

            stride = self.size_divisible
            max_size = list(max_size)
            max_size[1] = int(math.ceil(max_size[1] / stride) * stride)
            max_size[2] = int(math.ceil(max_size[2] / stride) * stride)
            max_size = tuple(max_size)

        batch_shape = (len(images),) + max_size
        batched_imgs = images[0].new(*batch_shape).zero_()
        for img, pad_img in zip(images, batched_imgs):
            pad_img[: img.shape[0], : img.shape[1], : img.shape[2]].copy_(img)

        image_sizes = [im.shape[-2:] for im in images]

        assert cur_count == len(batched_pos_map)
        positive_map = batched_pos_map.bool()

        return batched_imgs, image_sizes, new_caps, positive_map, entities, new_to_old_entity_list, new_entity_to_id_list, origin_images, ids, captions


class BBoxAugCollator(object):
    """
    From a list of samples from the dataset,
    returns the images and targets.
    Images should be converted to batched images in `im_detect_bbox_aug`
    """

    def __call__(self, batch):
        # return list(zip(*batch))
        transposed_batch = list(zip(*batch))

        images = transposed_batch[0]
        targets = transposed_batch[1]
        img_ids = transposed_batch[2]
        positive_map = None
        positive_map_eval = None

        if isinstance(targets[0], dict):
            return images, targets, img_ids, positive_map, positive_map_eval

        return images, targets, img_ids, positive_map, positive_map_eval
