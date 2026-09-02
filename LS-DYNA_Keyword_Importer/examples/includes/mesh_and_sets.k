*NODE
1, 0., 0., 0.
2, 1., 0., 0.
3, 1., 1., 0.
4, 0., 1., 0.
5, 0., 0., 1.
6, 1., 0., 1.
7, 1., 1., 1.
8, 0., 1., 1.
*ELEMENT_SOLID
1, 1, 1, 2, 3, 4, 5, 6, 7, 8
*PART
Steel cube
1, 1, 1, 0, 0, 0, 0, 0
*SECTION_SOLID
1, 1
*MAT_ELASTIC_TITLE
Steel demo material
1, 7.85E-9, 210000., 0.3
*SET_NODE_LIST_TITLE
Fixed face
10, 0., 0., 0., 0.
1, 4, 5, 8
*SET_NODE_LIST_TITLE
Loaded face
11, 0., 0., 0., 0.
2, 3, 6, 7
*SET_SEGMENT_TITLE
Top contact face
20, 0., 0., 0., 0.
5, 6, 7, 8
